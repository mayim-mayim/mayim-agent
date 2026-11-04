"""Fixed actual Backend modules over local HTTP; verifier/storage are doubles."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

import httpx
import pytest

from mayim_agent.activity_map import ActivityMapClient, ActivityMapError
from mayim_agent import activity_map_contract as c

BACKEND=(os.environ.get('MAG_BACKEND_C3_ROOT') or os.environ.get('MAG_BACKEND_C2_ROOT') or os.environ.get('MAG_BACKEND_C1_ROOT'))
pytestmark=pytest.mark.skipif(not BACKEND,reason='Fixed mAG Backend c1/c2/c3 modules required')
PINS={'agent-activity-map/control.mjs':'8b73c68e7a1338cc0eaf1803905e6d98aba2eedb6076726f9b1d25d94b32ac62',
      'agent-activity-map/http.mjs':'b752a4c6675d7099cec250fc453521ffd000213d559994d693d7118bc68e131f',
      'agent-activity-map/payment.mjs':'95459f4c42d1b15fbd871932b47560b87fe6052dff9c19dd91fcf94028b32fdc',
      'task-venue/rfc8785-jcs.mjs':'446522dde300cd4d4b0cbd806461427f8bf72b38f6cc24c9efb7a73c71f7d400'}

if os.environ.get('MAG_BACKEND_C3_ROOT'):
    PINS['agent-activity-map/control.mjs']='cb3ed623260ac4e9b222cb41ec2efade27f93dcfb6f1aafeee40f5b9a8da841e'

class Bridge(httpx.BaseTransport):
    def __init__(self, fixture='activity-map-http.mjs'):
        for path,expected in PINS.items():
            assert hashlib.sha256((Path(BACKEND)/'backend/shared'/path).read_bytes()).hexdigest()==expected
        self.proc=subprocess.Popen(['node',str(Path(__file__).parent/'fixtures/mag'/fixture)],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=dict(os.environ))
        self.port=json.loads(self.proc.stdout.readline())['port']
        self.transport=httpx.HTTPTransport(trust_env=False)
    def handle_request(self,request):
        assert request.url.host=='kari.mayim-mayim.com'
        request.url=request.url.copy_with(scheme='http',host='127.0.0.1',port=self.port)
        return self.transport.handle_request(request)
    def close(self):pass # ActivityMapClient opens one scoped HTTP client per request.
    def command(self,**body):
        with httpx.Client(trust_env=False) as client:
            r=client.post('http://127.0.0.1:%d/_test/control'%self.port,json=body);r.raise_for_status();return r.json()
    def stop(self):
        self.transport.close();self.proc.terminate();self.proc.communicate(timeout=5)

@pytest.fixture
def bridge():
    b=Bridge()
    try:yield b
    finally:b.stop()

class Runtime:
    """Synthetic signer only; SDK's Graph payment bridge remains real."""
    auto_navigate=True
    max_hops=3
    max_payment_retries=2
    agent_id='synthetic-agent'
    def __init__(self,rail='x402'):self.rail=rail;self.signs=[]
    def _capture_approved_receipt_snapshot(self,parsed):
        return {'canonical_snapshot_json':json.dumps({'rail':self.rail,'asset':'SATS' if self.rail=='L402' else 'USDC'})}
    def _process_payment(self,parsed,headers,payload,**kwargs):
        self.signs.append(self.rail)
        if self.rail=='x402':headers['Payment-Signature']='synthetic'
        else:headers['Authorization']=('L402 ' if self.rail=='L402' else 'Payment ')+'synthetic'
        return ('1'*64 if self.rail=='L402' else '0x'+'2'*64),'lightning' if self.rail=='L402' else 'eip155:8453',None
    def execute_detailed(self,method,path,payload,headers):
        first=self._request_sync(method,c.ORIGIN+path,{},None);assert first.status_code==402
        self._process_payment(None,headers,payload)
        return self._request_sync(method,c.ORIGIN+path,{},None)

def client(bridge,tmp_path,rail='x402'):
    return ActivityMapClient(tmp_path/'private',transport=bridge,payment_runtime=Runtime(rail),
        clock=lambda:bridge.command(action='counts')['now']/1000,sleep=lambda n:None,jitter=lambda:0)

@pytest.mark.parametrize('rail',['x402','L402','mpp'])
def test_actual_http_payment_and_all_parts_before_complete(bridge,tmp_path,rail):
    worker=client(bridge,tmp_path,rail)
    result=worker.download(scheme=rail,asset='SATS' if rail=='L402' else 'USDC')
    assert result['state']=='COMPLETE' and result['verified_parts']==2
    info=bridge.command(action='counts');assert info['settlements']==1
    assert len(worker.payment_runtime.signs)==1
    before=len(info['calls']);worker.resume(result['operation_id'])
    assert bridge.command(action='counts')['settlements']==1
    assert not any('/parts/' in x['path'] for x in bridge.command(action='counts')['calls'][before:])

def pending_acquisition(bridge,tmp_path):
    worker=client(bridge,tmp_path);bridge.command(action='fault',failPart=True)
    with pytest.raises(ActivityMapError) as e:worker.download(scheme='x402',asset='USDC')
    assert e.value.recovery
    return worker,e.value.recovery['operation_id']

def test_actual_restart_selective_recovery_and_lost_complete(bridge,tmp_path):
    worker,op=pending_acquisition(bridge,tmp_path)
    bridge.command(action='fault',loseComplete=True)
    with pytest.raises(ActivityMapError):worker.resume(op)
    bridge.command(action='fault');before=bridge.command(action='counts')
    restarted=ActivityMapClient(worker.directory,transport=bridge,clock=worker.clock,sleep=lambda n:None)
    result=restarted.resume(op);assert result['state']=='COMPLETE' and result['complete_notification']=='confirmed'
    after=bridge.command(action='counts');assert after['settlements']==1
    assert not any('/parts/' in x['path'] or x['path'].endswith('/manifest') for x in after['calls'][len(before['calls']):])

@pytest.mark.parametrize('replacement,decision,expected',[(False,None,'UNAVAILABLE'),(True,'decline','UNAVAILABLE'),(True,'accept','COMPLETE')])
def test_actual_stop_and_explicit_replacement(bridge,tmp_path,replacement,decision,expected):
    worker,op=pending_acquisition(bridge,tmp_path);old=worker._load(op)['purchase']
    bridge.command(action='revoke',replacement=replacement);bridge.command(action='fault')
    state=worker.resume(op)
    if replacement:
        assert state['state']=='REPLACEMENT_REQUIRED'
        candidate=state['replacement'];state=worker.replacement(op,decision=decision,
            generation=candidate['generation'],manifest_sha256=candidate['manifest_sha256'])
    assert state['state']==expected
    assert state['expires_at']==old['expires_at']
    assert bridge.command(action='counts')['settlements']==1

@pytest.mark.parametrize('fault',['stopAtComplete','expireAtComplete'])
def test_actual_complete_race_never_reports_success(bridge,tmp_path,fault):
    worker=client(bridge,tmp_path);bridge.command(action='fault',**{fault:True})
    with pytest.raises(ActivityMapError) as e:worker.download(scheme='x402',asset='USDC')
    assert worker.status(e.value.recovery['operation_id'])['state'] in ('UNAVAILABLE','EXPIRED_UNCONFIRMED')

def test_actual_pending_without_proof_never_restarts_payment(bridge,tmp_path):
    worker=ActivityMapClient(tmp_path/'private',transport=bridge,clock=lambda:1793491200,sleep=lambda n:None)
    result=worker.download(scheme='x402',asset='USDC');assert result['state']=='PAYMENT_PENDING'
    assert worker.resume(result['operation_id'])['state']=='PAYMENT_PENDING'
    assert bridge.command(action='counts')['verifications']==1

def test_real_mag_runtime_parser_policy_signer_and_backend_adapter(bridge,tmp_path):
    from mayim_agent.client import MagClient
    from mayim_agent.crypto.evm import LocalKeyAdapter
    from test_v1_16_2_payment_boundary import _exact_payload, _encode_requirement
    challenge=_exact_payload({'amount':'10000'}, {'resource':{'url':c.ORIGIN+c.PREFIX+'/purchases'}})
    bridge.command(action='challenge',body=challenge,headers={'PAYMENT-REQUIRED':_encode_requirement(challenge)})
    actual=LocalKeyAdapter('0x'+'1'*64) # Public synthetic test key, no provider call/broadcast.
    class Signer:
        address=actual.address
        calls=0
        def generate_eip3009_payload_atomic(self,**kwargs):
            self.calls+=1
            return actual.generate_eip3009_payload_atomic(**kwargs)
    signer=Signer();runtime=MagClient(evm_signer=signer)
    runtime._clock=lambda:bridge.command(action='counts')['now']/1000
    worker=ActivityMapClient(tmp_path/'private',transport=bridge,payment_runtime=runtime,
        clock=runtime._clock,sleep=lambda n:None)
    result=worker.download(scheme='exact',asset='USDC')
    assert result['state']=='COMPLETE' and signer.calls==1
    saved=worker._load(result['operation_id'])
    assert saved['payment_identity']['approved_requirement']['rail']=='x402'
    assert saved['payment_headers']['PAYMENT-SIGNATURE']
    assert worker.resume(result['operation_id'])['state']=='COMPLETE' and signer.calls==1

def test_lost_settlement_response_reuses_original_proof(bridge,tmp_path):
    worker=client(bridge,tmp_path);bridge.command(action='fault',losePurchase=True)
    with pytest.raises(ActivityMapError) as error:worker.download(scheme='x402',asset='USDC')
    operation=error.value.recovery['operation_id'];saved=worker._load(operation)
    assert saved['payment_headers'] and len(worker.payment_runtime.signs)==1
    bridge.command(action='fault')
    assert worker.resume(operation)['state']=='COMPLETE'
    assert bridge.command(action='counts')['settlements']==1 and len(worker.payment_runtime.signs)==1

def test_confirmed_complete_after_deadline_does_not_reissue_payload(bridge,tmp_path):
    worker=client(bridge,tmp_path);first=worker.download(scheme='x402',asset='USDC')
    before=bridge.command(action='advance',ms=86400001)
    result=worker.resume(first['operation_id']);assert result['state']=='COMPLETE'
    assert result['expires_at']==first['expires_at']
    after=bridge.command(action='counts')
    assert after['settlements']==1
    assert not any('/parts/' in x['path'] or x['path'].endswith('/manifest') for x in after['calls'][len(before['calls']):])

def test_midnight_does_not_replace_purchased_window(bridge,tmp_path):
    worker,operation=pending_acquisition(bridge,tmp_path);old=worker._load(operation)['purchase']
    bridge.command(action='advance',ms=16*3600000);bridge.command(action='fault')
    result=worker.resume(operation)
    assert result['state']=='COMPLETE' and result['generation']==old['generation']
    assert result['expires_at']==old['expires_at']
