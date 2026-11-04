"""SDK public consumers -> fixed final Graph/Hono handlers; synthetic physical I/O.

MAG_C6_HANDLER_ROOT is prepared from the independently pinned Test bridge and
Backend 8729eede's final ZIPs. No response DTO is simplified or rewritten.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
import mayim_agent
from mayim_agent.activity_map import ActivityMapClient, ActivityMapError
from mayim_agent.client import MagClient

ROOT = os.environ.get('MAG_C6_HANDLER_ROOT')
pytestmark = pytest.mark.skipif(not ROOT, reason='Fixed final Graph/Hono artifacts required')
ENTRY = Path(__file__).parent / 'fixtures/mag/c6/surface-entry.py'
PACKAGE = str(Path(mayim_agent.__file__).resolve().parent.parent)


class Bridge:
    def __init__(self, directory, loss):
        self.directory, self.loss = directory, loss
        self.lock = threading.Lock()
        self.trace, self.challenges, self.saved_challenges = [], [], []
        self.stderr = (directory.parent / 'bridge.stderr').open('w')
        bridge = Path(ROOT) / 'hono-graph-c1-evidence/payment/sdk-bridge-used.mjs'
        assert hashlib.sha256(bridge.read_bytes()).hexdigest() == '379bb49b50b410a1e5c882594735b4fe222045f047087285b04fd58d7a12f883'
        self.node = subprocess.Popen(['node', str(bridge)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=self.stderr, text=True, bufsize=1)
        self.now = time.time()
        self.command(op='state', values={'now': int(self.now * 1000)})
        parent = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self): self.forward()
            def do_POST(self): self.forward()
            def forward(self):
                body = self.rfile.read(int(self.headers.get('content-length', 0))).decode()
                result = parent.request(self.command, self.path, dict(self.headers), body or None)
                data = result['body'].encode()
                self.send_response(result['status'])
                for key, value in result.get('headers', {}).items():
                    if key.lower() not in ('content-length', 'transfer-encoding', 'connection'):
                        self.send_header(key, value)
                self.send_header('content-length', str(len(data))); self.end_headers(); self.wfile.write(data)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()

    def command(self, **value):
        with self.lock:
            self.node.stdin.write(json.dumps(value) + '\n'); self.node.stdin.flush()
            raw = self.node.stdout.readline()
            assert raw, 'Fixed handler bridge ended; see bridge.stderr'
            result = json.loads(raw)
            assert 'harness_error' not in result, result
            return result

    def request(self, method, path, headers, body):
        if method == 'POST' and path.endswith('/purchases'):
            d = json.loads(next(self.directory.glob('*.activity-map.json')).read_bytes())['payload']
            h = {k.lower():v for k,v in headers.items()}
            assert body == d['request_bytes']
            assert json.loads(body)['operation_id'] == d['operation_id']
            assert h['authorization'] == 'MAG-Recovery ' + d['recovery_secret']
        if (path.endswith('/recover') or path.endswith('/purchases')) and not self.saved_challenges:
            for p in self.directory.glob('*.activity-map.json'):
                d = json.loads(p.read_text())['payload']
                if d.get('payment_started'):
                    challenge = d['purchase']['payment_challenge']
                    assert type(challenge['challenge']['amount']) is float
                    assert challenge['challenge']['amount'] == 0.01
                    assert challenge == json.loads(self.challenges[0])['payment_challenge']
                    self.saved_challenges.append(challenge)
        result = self.command(op='request', method=method, path=path, headers=headers,
                              **({'body': body} if body else {}))
        self.trace.append({'method': method, 'path': path, 'status': result['status']})
        if result['status'] == 402: self.challenges.append(result['body'])
        return result

    def transport(self, req):
        assert req.url.host == 'kari.mayim-mayim.com'
        path = req.url.raw_path.decode()
        r = self.request(req.method, path, dict(req.headers), req.content.decode() if req.content else None)
        if ((self.loss == 'payment' and path.endswith('/purchases') and r['status'] != 402)
                or (self.loss == 'complete' and path.endswith('/complete'))):
            raise httpx.ReadError('synthetic response loss after real handler')
        return httpx.Response(r['status'], headers=r.get('headers', {}), content=r['body'].encode())

    def child(self, surface, *args):
        return [sys.executable, '-I', str(ENTRY), PACKAGE, surface, *args]

    def env(self, signing=False):
        env = {k:v for k,v in os.environ.items() if k not in ('AGENT_PRIVATE_KEY','LN_API_URL','LN_API_KEY','PYTHONPATH','PYTHONHOME')}
        env.update(SDK_GRAPH_BRIDGE_PORT=str(self.server.server_address[1]), SDK_GRAPH_CLOCK=str(self.now))
        if signing: env['AGENT_PRIVATE_KEY'] = '11' * 32
        return env

    def cli(self, action, operation=None):
        args = ['graph', action, '--directory', str(self.directory)]
        args += ['--operation-id', operation] if operation else ['--scheme','x402','--asset','USDC']
        p = subprocess.run(self.child('cli', *args), env=self.env(action=='download'), capture_output=True, text=True, timeout=40)
        assert p.returncode == 0, (p.stdout, p.stderr)
        return json.loads(p.stdout)

    def mcp(self, name, operation=None):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        async def call():
            argv = self.child('execution')
            async with stdio_client(StdioServerParameters(command=argv[0], args=argv[1:], env=self.env(operation is None))) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    args = dict(directory=str(self.directory))
                    args.update(dict(operation_id=operation) if operation else dict(scheme='x402',asset='USDC'))
                    result = await session.call_tool(name, args)
                    assert not result.isError, result
                    return result.structuredContent or json.loads(next(x.text for x in result.content if x.type=='text'))
        return asyncio.run(asyncio.wait_for(call(), 40))

    def close(self):
        self.server.shutdown(); self.server.server_close()
        self.node.terminate(); self.node.communicate(timeout=10); self.stderr.close()


@pytest.mark.parametrize('surface,loss', [(surface, loss)
    for surface in ('sync','async') for loss in ('none','payment','complete')]
    + [('cli','none'), ('mcp','none')])
def test_real_402_purchase_and_keyless_process_recovery(tmp_path, monkeypatch, surface, loss):
    directory = tmp_path / 'journal'
    bridge = Bridge(directory, loss)
    try:
        original = ActivityMapClient.__init__
        def local(self, directory, **kwargs):
            kwargs.update(transport=httpx.MockTransport(bridge.transport), clock=lambda:bridge.now,
                          sleep=lambda _:None, jitter=lambda:0)
            original(self, directory, **kwargs)
        monkeypatch.setattr(ActivityMapClient, '__init__', local)
        runtime = MagClient(private_key='11'*32, auto_navigate=False)
        runtime._clock = lambda:bridge.now
        signed = []
        signer = runtime.evm_signer.generate_eip3009_payload_atomic
        def signing(**kwargs):
            d = ActivityMapClient(directory)._load(next(directory.glob('*.activity-map.json')).name.split('.')[0])
            assert d['purchase']['payment_challenge']['challenge']['amount'] == 0.01
            signed.append(kwargs['atomic_amount_str'])
            return signer(**kwargs)
        runtime.evm_signer.generate_eip3009_payload_atomic = signing
        def download():
            if surface == 'cli': return bridge.cli('download')
            if surface == 'mcp': return bridge.mcp('download_activity_map')
            kwargs = dict(scheme='x402',asset='USDC')
            if surface == 'async': return asyncio.run(runtime.download_activity_map_async(directory, **kwargs))
            return runtime.download_activity_map(directory, **kwargs)
        if loss == 'none':
            result = download(); assert result['state'] == 'COMPLETE'
            operation = result['operation_id']
        else:
            with pytest.raises(ActivityMapError) as error: download()
            operation = error.value.recovery['operation_id']
        worker = ActivityMapClient(directory)
        before = worker._load(operation)
        approved = before['payment_identity']['approved_requirement']
        assert approved['idempotency_key'] == operation
        assert approved['method'] == 'POST'
        assert approved['resource_url'] == 'https://kari.mayim-mayim.com/api/agent/activity-map/v1/purchases'
        assert approved['network'] == 'eip155:137' and approved['amount_atomic'] == '10000'
        fields = ('operation_id','request','request_bytes','request_sha256','payment_identity','payment_headers','generation','manifest_sha256','recovery_secret')
        invariant = {k:before[k] for k in fields}
        assert hashlib.sha256(before['request_bytes'].encode()).hexdigest() == before['request_sha256']
        assert bridge.saved_challenges and bridge.challenges
        assert bridge.command(op='inspect')['counters']['providerSettle'] == 1
        bridge.loss = 'none'
        cli = bridge.cli('resume', operation)
        mcp = bridge.mcp('activity_map_status', operation)
        assert cli['state'] == mcp['state'] == 'COMPLETE'
        assert cli['verified_parts'] > 0
        after = worker._load(operation)
        assert {k:after[k] for k in fields} == invariant
        counters = bridge.command(op='inspect')['counters']
        assert counters['providerSettle'] == 1
        # One challenge POST, then proof POST(s) for the same saved request.
        # Response-loss retries must never dispatch a second provider payment.
        assert len([x for x in bridge.trace if x['method']=='POST' and x['path'].endswith('/purchases')]) == (4 if loss == 'payment' else 2)
        if surface in ('sync','async'): assert signed == ['10000']
        if os.environ.get('MAG_C6_OUTPUT'):
            out = Path(os.environ['MAG_C6_OUTPUT']); out.mkdir(parents=True,exist_ok=True)
            report = dict(surface=surface,loss=loss,package=mayim_agent.__file__,provider_settlements=1,
                saved_numeric_amount=0.01,atomic_amount='10000',binding_preserved=True,
                cli_state=cli['state'],mcp_state=mcp['state'],trace=bridge.trace,
                raw_402_sha256=[hashlib.sha256(x.encode()).hexdigest() for x in bridge.challenges])
            (out/(surface+'-'+loss+'.json')).write_text(json.dumps(report,indent=2)+'\n')
    finally:
        if os.environ.get('MAG_C6_OUTPUT'):
            out = Path(os.environ['MAG_C6_OUTPUT']); out.mkdir(parents=True, exist_ok=True)
            saved = []
            for path in directory.glob('*.activity-map.json'):
                value = json.loads(path.read_text())['payload']
                challenge = (value.get('purchase') or {}).get('payment_challenge')
                saved.append(dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    payment_started=value['payment_started'], proof_present=bool(value['payment_headers']),
                    numeric_amount=(challenge or {}).get('challenge', {}).get('amount')))
            (out/(surface+'-'+loss+'-observation.json')).write_text(json.dumps(dict(
                package=mayim_agent.__file__, trace=bridge.trace,
                counters=bridge.command(op='inspect')['counters'], saved=saved,
                raw_402=bridge.challenges), indent=2)+'\n')
        bridge.close()


@pytest.mark.parametrize('surface', ['sync','async'])
@pytest.mark.parametrize('checkpoint', ['challenge','proof'])
def test_saved_checkpoint_recovers_without_starting_payment(tmp_path, monkeypatch, surface, checkpoint):
    """A crash before proof dispatch cannot be treated as payment permission.

    /recover and keyless status stay pending, preserving the original operation
    and any saved proof. Only the normal purchase flow may start settlement.
    """
    directory = tmp_path / 'journal'; bridge = Bridge(directory,'none')
    try:
        original_init=ActivityMapClient.__init__; original_save=ActivityMapClient._save
        def local(self,directory,**kwargs):
            kwargs.update(transport=httpx.MockTransport(bridge.transport),clock=lambda:bridge.now,
                          sleep=lambda _:None,jitter=lambda:0)
            original_init(self,directory,**kwargs)
        monkeypatch.setattr(ActivityMapClient,'__init__',local)
        crashed=[]
        def save(self,d):
            original_save(self,d)
            eligible = bool(d.get('purchase')) if checkpoint=='challenge' else bool(d['payment_headers'])
            if eligible and not crashed:
                crashed.append(True)
                raise RuntimeError('Synthetic process interruption after atomic journal save')
        monkeypatch.setattr(ActivityMapClient,'_save',save)
        runtime=MagClient(private_key='11'*32,auto_navigate=False);runtime._clock=lambda:bridge.now
        with pytest.raises(ActivityMapError) as error:
            if surface=='sync': runtime.download_activity_map(directory,scheme='x402',asset='USDC')
            else: asyncio.run(runtime.download_activity_map_async(directory,scheme='x402',asset='USDC'))
        operation=error.value.recovery['operation_id']; worker=ActivityMapClient(directory)
        before=worker._load(operation)
        assert before['purchase']['payment_challenge']==json.loads(bridge.challenges[0])['payment_challenge']
        assert bool(before['payment_headers']) == (checkpoint=='proof')
        fields=('operation_id','request_bytes','request_sha256','payment_headers','payment_identity','recovery_secret')
        invariant={k:before[k] for k in fields}
        cli=bridge.cli('resume',operation);mcp=bridge.mcp('activity_map_status',operation)
        assert cli['state']==mcp['state']=='PAYMENT_PENDING'
        assert {k:worker._load(operation)[k] for k in fields}==invariant
        counters=bridge.command(op='inspect')['counters']
        assert counters.get('providerSettle',0)==counters.get('providerVerify',0)==0
        assert len([r for r in bridge.trace if r['method']=='POST' and r['path'].endswith('/purchases')])==1
        if os.environ.get('MAG_C6_OUTPUT'):
            out=Path(os.environ['MAG_C6_OUTPUT']);out.mkdir(parents=True,exist_ok=True)
            (out/(surface+'-saved-'+checkpoint+'.json')).write_text(json.dumps(dict(
                package=mayim_agent.__file__,surface=surface,checkpoint=checkpoint,
                saved_numeric_amount=0.01,proof_saved=bool(before['payment_headers']),
                cli_state=cli['state'],mcp_state=mcp['state'],binding_preserved=True,
                provider_settlements=0,trace=bridge.trace),indent=2)+'\n')
    finally: bridge.close()


@pytest.mark.parametrize('field', ['operation_id','generation','manifest_sha256'])
def test_purchase_binding_mismatch_stops_before_payment(tmp_path, monkeypatch, field):
    directory=tmp_path/'journal';bridge=Bridge(directory,'none')
    try:
        def transport(req):
            result=bridge.transport(req)
            if result.status_code==402:
                body=result.json();body[field]='0'*len(body[field])
                return httpx.Response(402,headers=result.headers,json=body)
            return result
        runtime=MagClient(private_key='11'*32,auto_navigate=False)
        worker=ActivityMapClient(directory,payment_runtime=runtime,transport=httpx.MockTransport(transport),clock=lambda:bridge.now)
        with pytest.raises(ActivityMapError,match='PURCHASE_BINDING_ERROR'):
            worker.download(scheme='x402',asset='USDC')
        saved=next(directory.glob('*.activity-map.json'))
        assert json.loads(saved.read_bytes())['payload']['payment_started'] is False
        assert bridge.command(op='inspect')['counters'].get('providerSettle',0)==0
        assert len(bridge.trace)==2
    finally: bridge.close()
