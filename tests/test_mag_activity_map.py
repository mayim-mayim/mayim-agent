"""Isolated Graph wire tests derived from specification §6 and A07–A09."""
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone

import httpx
import pytest

from mayim_agent.activity_map import ActivityMapClient, ActivityMapError
from mayim_agent import activity_map_contract as c


def raw(value):
    return json.dumps(value, ensure_ascii=False, indent=1).encode()


def desc(value):
    digest=hashlib.sha256(value).hexdigest()
    return dict(part_id=digest,sha256=digest,bytes=len(value))


class Server:
    def __init__(self):
        self.now=1793491200.0
        self.generation='a'*32
        self.payloads=[b'{"items":["UTF-8: \\u65e5"]}',b'{"items":[2]}']
        catalog=raw(dict(schema_version='mag.activity-map.part-catalog.v1',parts=[desc(x) for x in self.payloads]))
        self.parts={desc(x)['part_id']:x for x in self.payloads+[catalog]}
        window=dict(start='2026-10-02T15:00:00Z',end='2026-11-01T15:00:00Z',timezone='Asia/Tokyo')
        self.manifest=raw(dict(schema_version='mag.activity-map.manifest.v1',generation=self.generation,
            period='30d',window=window,windows={x:window for x in ('today','7d','30d')},
            cutoff='2026-11-01T00:00:00Z',policy_id='mag.activity-map.public.v1',
            source_coverage={'scheduled':'not_confirmed'},coverage_complete=False,parts=[desc(catalog)],
            parts_format='one-level-catalog',uncompressed_encoding='UTF-8',hash_algorithm='SHA-256',total_known=None))
        self.calls=[];self.p=None;self.secret=None;self.fail_parts=set();self.lose_complete=False
        self.pending=False;self.complete_requests=0;self.status_override=None;self.corrupt=False
        self.retry=None;self.client=None;self.replacement=None;self.lost_replace=False

    def handle(self,r):
        path=r.url.path.removeprefix(c.PREFIX);self.calls.append((r.method,path))
        if self.retry:
            return httpx.Response(self.retry,headers={'Retry-After':'4'})
        if path=='/manifest':
            return httpx.Response(200,content=self.manifest)
        if path=='/purchases':
            body=json.loads(r.content);operation=body['operation_id']
            # Journal must precede first purchase; no secrets in its result.
            d=self.client._load(operation)
            assert d['request']==body and len(d['recovery_secret'])==64
            self.secret=r.headers['authorization']
            self.p=dict(schema_version='mag.activity-map.purchase.v1',purchase_id=operation,operation_id=operation,
                generation=self.generation,manifest_sha256=hashlib.sha256(self.manifest).hexdigest(),
                confirmed_at=None if self.pending else '2026-11-01T00:00:00Z',
                expires_at=None if self.pending else '2026-11-02T00:00:00Z',
                state='PAYMENT_PENDING' if self.pending else 'ACQUIRING',reason=None,actions=['recover'] if self.pending else ['manifest','parts','complete'])
            if self.pending:
                return httpx.Response(402,json=dict(self.p,payment_challenge={'status':'payment_required'}))
            return httpx.Response(200,json=self.p)
        assert r.headers['authorization']==self.secret
        assert 'paymentOverride' not in json.loads(r.content or '{}')
        assert 'X-PAYMENT' not in r.headers
        if path.endswith('/recover'):
            assert json.loads(r.content)=={'operation_id':self.p['operation_id']}
            return httpx.Response(202 if self.pending else 200,json=self.p)
        if path.endswith('/replacement'):
            body=json.loads(r.content)
            if body['decision']=='decline':
                self.p.update(state='UNAVAILABLE',reason='replacement_declined',actions=['status'])
            else:
                self.p.update(state='ACQUIRING',generation=self.replacement['generation'],
                              manifest_sha256=self.replacement['manifest_sha256'],actions=['manifest','parts','complete'])
            if self.lost_replace:
                raise httpx.ReadError('synthetic response loss')
            return httpx.Response(200,json=self.p)
        if path.endswith('/complete'):
            self.complete_requests+=1
            assert json.loads(r.content)==dict(generation=self.p['generation'],manifest_sha256=self.p['manifest_sha256'])
            # Backend state confirmation is distinct from file receipt.
            self.p.update(state='COMPLETE',actions=['status'])
            if self.lose_complete:
                raise httpx.ReadError('synthetic complete response loss')
            return httpx.Response(200,json=self.p)
        if path.endswith('/manifest'):
            assert self.p['state']=='ACQUIRING'
            return httpx.Response(200,content=self.manifest)
        if '/parts/' in path:
            assert self.p['state']=='ACQUIRING'
            part=path.rsplit('/',1)[1]
            if part in self.fail_parts:
                return httpx.Response(503)
            return httpx.Response(200,content=b'corrupt' if self.corrupt else self.parts[part])
        return httpx.Response(self.status_override or 200,json=self.p)

    def connect(self,tmp_path):
        d=tmp_path/'private';d.mkdir(mode=0o700)
        self.delays=[]
        self.client=ActivityMapClient(d,transport=httpx.MockTransport(self.handle),clock=lambda:self.now,
            sleep=self.delays.append,jitter=lambda:0)
        return self.client


def test_download_saves_exact_bytes_then_complete(tmp_path):
    s=Server();client=s.connect(tmp_path)
    result=client.download(scheme='x402',asset='USDC')
    assert result['state']=='COMPLETE' and result['verified_parts']==3
    assert result['complete_notification']=='confirmed' and s.complete_requests==1
    directory=Path(result['saved_directory'])
    assert (directory/'manifest.json').read_bytes()==s.manifest
    for key,value in s.parts.items():
        assert (directory/key).read_bytes()==value
    assert 'recovery_secret' not in json.dumps(result)
    assert os.stat(client._path(result['operation_id'])).st_mode & 0o777 == 0o600


def test_missing_parts_resume_without_payment_or_good_part_refetch(tmp_path):
    s=Server();client=s.connect(tmp_path);bad=desc(s.payloads[1])['part_id'];s.fail_parts.add(bad)
    with pytest.raises(ActivityMapError) as failure:client.download(scheme='x402',asset='USDC')
    assert s.complete_requests==0
    assert failure.value.recovery == {'operation_id':s.p['operation_id'],'directory':str(client.directory)}
    assert s.secret not in str(failure.value)
    op=s.p['operation_id'];before=list(s.calls);s.fail_parts.clear()
    result=client.resume(op)
    assert result['state']=='COMPLETE'
    subsequent=s.calls[len(before):]
    assert ('POST','/purchases') not in subsequent
    assert sum('/parts/'+bad==p.removeprefix('/purchases/'+op) for _,p in subsequent)==1
    assert not any(desc(s.payloads[0])['part_id'] in p for _,p in subsequent)


def test_corrupt_part_never_complete(tmp_path):
    s=Server();s.corrupt=True;client=s.connect(tmp_path)
    with pytest.raises(ActivityMapError):client.download(scheme='L402',asset='SATS')
    assert s.complete_requests==0


def test_lost_complete_response_recovers_status_without_new_payload(tmp_path):
    s=Server();s.lose_complete=True;client=s.connect(tmp_path)
    with pytest.raises(ActivityMapError):client.download(scheme='x402',asset='USDC')
    before=len(s.calls);s.lose_complete=False
    result=client.resume(s.p['operation_id'])
    assert result['state']=='COMPLETE'
    assert not any('/parts/' in p or p.endswith('/manifest') or p=='/purchases' for _,p in s.calls[before:])


def test_pending_recovery_never_repeats_purchase(tmp_path):
    s=Server();s.pending=True;client=s.connect(tmp_path)
    result=client.download(scheme='x402',asset='USDC')
    assert result['state']=='PAYMENT_PENDING'
    client.payment_runtime=object() # No usable signer: resume must not touch it.
    assert client.resume(result['operation_id'])['state']=='PAYMENT_PENDING'
    assert sum(p=='/purchases' for _,p in s.calls)==1


def test_429_honors_retry_after_and_stops_at_three(tmp_path):
    s=Server();s.retry=429;client=s.connect(tmp_path)
    with pytest.raises(ActivityMapError):client.download(scheme='x402',asset='USDC')
    assert len(s.calls)==3 and s.delays==[4,4]


def test_expired_purchase_does_not_fetch_payload(tmp_path):
    s=Server();client=s.connect(tmp_path);s.fail_parts.add(desc(s.payloads[0])['part_id'])
    with pytest.raises(ActivityMapError):client.download(scheme='x402',asset='USDC')
    s.now+=86400;s.fail_parts.clear();s.p.update(state='EXPIRED_UNCONFIRMED',actions=['status'])
    before=len(s.calls)
    assert client.resume(s.p['operation_id'])['state']=='EXPIRED_UNCONFIRMED'
    assert len(s.calls)==before+1


def test_401_remains_auth_failure_not_unavailable(tmp_path):
    s=Server();client=s.connect(tmp_path);client.download(scheme='x402',asset='USDC')
    s.status_override=401;before=len(s.calls)
    with pytest.raises(ActivityMapError) as e:client.status(s.p['operation_id'])
    assert e.value.status_code==401 and len(s.calls)==before+1


def test_decline_replacement_preserves_payment_and_expiry(tmp_path):
    s=Server();client=s.connect(tmp_path);s.fail_parts.add(desc(s.payloads[0])['part_id'])
    with pytest.raises(ActivityMapError):client.download(scheme='x402',asset='USDC')
    s.p.update(state='REPLACEMENT_REQUIRED',actions=['replacement','decline'],replacement={'generation':'b'*32})
    result=client.replacement(s.p['operation_id'],decision='decline')
    assert result['state']=='UNAVAILABLE' and result['confirmed_at']=='2026-11-01T00:00:00Z'
    assert result['expires_at']=='2026-11-02T00:00:00Z'


def test_replacement_uses_separate_generation_and_exact_scope(tmp_path):
    s=Server();client=s.connect(tmp_path);s.fail_parts.add(desc(s.payloads[0])['part_id'])
    with pytest.raises(ActivityMapError):client.download(scheme='x402',asset='USDC')
    op=s.p['operation_id'];old=client.directory/op/s.generation
    m=json.loads(s.manifest);m['generation']='b'*32;s.manifest=raw(m)
    s.replacement=dict(generation='b'*32,manifest_sha256=hashlib.sha256(s.manifest).hexdigest())
    s.p.update(state='REPLACEMENT_REQUIRED',actions=['replacement','decline'],replacement=s.replacement)
    s.fail_parts.clear()
    result=client.replacement(op,decision='accept',**s.replacement)
    assert result['state']=='COMPLETE' and result['generation']=='b'*32
    assert old.exists() and Path(result['saved_directory'])!=old
    assert result['expires_at']=='2026-11-02T00:00:00Z'


def test_failed_journal_write_prevents_first_purchase(tmp_path,monkeypatch):
    s=Server();client=s.connect(tmp_path)
    def fail(*args):raise OSError('synthetic disk failure')
    monkeypatch.setattr(client,'_save',fail)
    with pytest.raises(ActivityMapError):client.download(scheme='x402',asset='USDC')
    assert s.calls==[('GET','/manifest')]


def test_lost_replacement_response_reuses_original_decision(tmp_path):
    s=Server();client=s.connect(tmp_path);s.fail_parts.add(desc(s.payloads[0])['part_id'])
    with pytest.raises(ActivityMapError):client.download(scheme='x402',asset='USDC')
    op=s.p['operation_id'];m=json.loads(s.manifest);m['generation']='b'*32;s.manifest=raw(m)
    s.replacement=dict(generation='b'*32,manifest_sha256=hashlib.sha256(s.manifest).hexdigest())
    s.p.update(state='REPLACEMENT_REQUIRED',actions=['replacement','decline'],replacement=s.replacement)
    s.fail_parts.clear();s.lost_replace=True
    with pytest.raises(ActivityMapError):client.replacement(op,decision='accept',**s.replacement)
    s.lost_replace=False
    assert client.resume(op)['state']=='COMPLETE'
    assert sum(path=='/purchases' for _,path in s.calls)==1


def test_async_status_and_execution_mcp_boundary(tmp_path):
    import asyncio
    from mayim_agent.integrations import mcp, mcp_inspect, mcp_structured_response
    s=Server();client=s.connect(tmp_path)
    result=asyncio.run(client.download_async(scheme='x402',asset='USDC'))
    assert asyncio.run(client.status_async(result['operation_id']))['state']=='COMPLETE'
    wanted={'download_activity_map','resume_activity_map','activity_map_status','replace_activity_map'}
    assert wanted <= {x.name for x in asyncio.run(mcp.mcp.list_tools())}
    assert not wanted & {x.name for x in asyncio.run(mcp_inspect.mcp.list_tools())}
    from mcp.server.fastmcp import FastMCP
    responder = FastMCP('test-responder')
    mcp_structured_response.register_tools(responder)
    assert not wanted & {x.name for x in asyncio.run(responder.list_tools())}


def test_existing_payment_runtime_is_called_once_and_proof_saved_before_dispatch(tmp_path):
    from mayim_agent.activity_map_payment import pay_once
    s=Server();s.pending=True;client=s.connect(tmp_path)
    pending=client.download(scheme='x402',asset='USDC');d=client._load(pending['operation_id'])
    calls=[]
    class Runtime:
        auto_navigate=True
        max_hops=3
        max_payment_retries=2
        def _capture_approved_receipt_snapshot(self,parsed):
            return {'canonical_snapshot_json':json.dumps({'rail':'x402','network':'eip155:8453','asset':'USDC','reference':'synthetic'})}
        def _process_payment(self,parsed,headers,payload,**kwargs):
            calls.append('sign')
            assert client._load(d['operation_id'])['payment_started']
            headers['Payment-Signature']='synthetic-not-a-real-payment'
            return '0x'+'1'*64,'eip155:8453',None
        def execute_detailed(self,method,path,payload,headers):
            response=self._request_sync(method,c.ORIGIN+path,{},None)
            assert response.status_code==402
            self._process_payment(None,headers,payload)
            saved=client._load(d['operation_id'])
            assert saved['payment_headers']=={'Payment-Signature':'synthetic-not-a-real-payment'}
            assert saved['payment_identity']['reference']=='0x'+'1'*64
            self._request_sync(method,c.ORIGIN+path,{},None)
    runtime=Runtime();client.payment_runtime=runtime
    # This fixture deliberately never authorizes payment. The initial proof
    # belongs on the same purchase POST; a fresh 402 must not trigger signing
    # again. /recover is reserved for the later, non-charging lookup.
    with pytest.raises(ActivityMapError,match='PAYMENT_UNCONFIRMED'):
        pay_once(client,d,{},raw(dict(s.p,payment_challenge={'status':'synthetic'})))
    assert calls==['sign']
    assert runtime.auto_navigate is True and runtime.max_hops==3
    assert client.resume(d['operation_id'])['state']=='PAYMENT_PENDING'
    assert calls==['sign']


def test_restart_metadata_is_bounded_and_rejects_links(tmp_path):
    from mayim_agent.activity_map import _read_metadata, _file_valid
    metadata = tmp_path / 'manifest.json'
    metadata.write_bytes(b'{}')
    assert _read_metadata(metadata, 2) == b'{}'
    with metadata.open('r+b') as stream:
        stream.truncate(1024 * 1024 * 1024)
    with pytest.raises(ActivityMapError, match='METADATA_INVALID'):
        _read_metadata(metadata, 16 * 1024 * 1024)
    assert not _file_valid(metadata, desc(b'{}'))
    metadata.unlink()
    real = tmp_path / 'actual.json'
    real.write_bytes(b'{}')
    metadata.symlink_to(real)
    with pytest.raises((ValueError, OSError, ActivityMapError)):
        _read_metadata(metadata, 2)
    metadata.unlink()
    os.link(real, metadata)
    with pytest.raises(ActivityMapError, match='METADATA_INVALID'):
        _read_metadata(metadata, 2)
