"""Exact c4 producer -> SDK consumer. No funded wallet or external network."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import sys
import uuid
from urllib.parse import urlsplit, parse_qsl

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data
from mayim_agent.crypto.evm import LocalKeyAdapter
from mayim_agent.offer_registration import OfferRegistrationClient, OfferRegistrationError
from mayim_agent.immediate_visit_transport import ImmediateVisitTransport, ImmediateVisitRawResponse
from mayim_agent.task_transport import TaskTransport
from mayim_agent.task_client import AgentTaskClient
from mayim_agent.task_models import TaskClaimCredential, AgentTaskClaimResponse, AgentTask, TaskDomainObservationSubmission
from mayim_agent.task_v2_client import AgentTaskV2Client
from mayim_agent.task_v2_transport import TaskV2Transport, TaskV2RawResponse
from mayim_agent.task_v2_models import ScheduledTaskClaimCredential
from mayim_agent._contract_schema import ECONOMIC_PACKS, validate_economic_wire

ROOT=Path(__file__).parent
pytestmark=pytest.mark.skipif(not os.environ.get('MAG_BACKEND_C4_ROOT'), reason='Exact isolated Backend c4 required')
SEED='17'*32  # Public unfunded synthetic signing fixture, never used outside subprocess.

class Bridge:
    def __init__(self):
        self.proc=subprocess.Popen(['node',str(ROOT/'fixtures/mag/sdk-c3-producer.mjs')],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.calls=[]
    def send(self, value):
        self.proc.stdin.write(json.dumps(value)+'\n'); self.proc.stdin.flush()
        while True:
            line=self.proc.stdout.readline()
            if not line: raise AssertionError('Bridge failed: '+self.proc.stderr.read()[-1500:])
            if line.startswith('{'): break
        r=json.loads(line)
        assert 'bridge_error' not in r, r
        return r
    def exchange(self,method,path,query,headers,body,timeout=20,**kw):
        self.calls.append((method,path))
        r=self.send(dict(method=method,path=path,query=dict(parse_qsl(query or '')),headers=headers,body=body.decode()))
        return ImmediateVisitRawResponse(r['status'],r.get('headers',{}),json.dumps(r['body']).encode())
    def scheduled(self,method,path,query,headers,body):
        r=self.exchange(method,path,query,headers,body)
        return TaskV2RawResponse(r.status_code,r.headers,r.body)
    def task(self,**kw):
        self.calls.append((kw['method'], urlsplit(kw['url']).path))
        r=self.send(dict(method=kw['method'],path=urlsplit(kw['url']).path,query=kw['params'],
            headers={'x-ln-task-claim-token':kw['claim_token']} if kw['claim_token'] else {},body=(kw['body'] or b'').decode()))
        return r['status'],{'content-type':'application/json',**r.get('headers',{})},json.dumps(r['body']).encode()
    def client(self):
        return OfferRegistrationClient(transport=ImmediateVisitTransport(exchange=self.exchange))
    def close(self):
        self.proc.stdin.close();self.proc.wait(timeout=10)
        assert self.proc.returncode==0,self.proc.stderr.read()[-1000:]

@pytest.fixture
def bridge():
    b=Bridge()
    try: yield b
    finally: b.close()

def request(epoch='isolated-c4'):
    return dict(schema_version='ln_church.task_offer_create_request.payment_surface_discovery.v1_1',
        task_type='payment_surface_discovery.v1',task_definition_version='1.1.0',
        task_definition_digest=ECONOMIC_PACKS['discovery_mag']['digest'],plan_id='C50',
        activity_map_policy_id='mag.activity-map.public.v1',activity_map_epoch=epoch)

def signature(typed):
    return '0x'+Account.from_key(SEED).sign_message(encode_typed_data(full_message=typed)).signature.hex().removeprefix('0x')

def test_discovery_registration_claim_saved_conditions_and_original_recovery(bridge):
    reg=bridge.client();quote=reg.prepare_registration(request())
    assert '30 JST' in quote.activity_map_notice
    op=quote.sign(LocalKeyAdapter(SEED));result=reg.submit_registration(op)
    assert len(op.operation_ref)==64 and result['task_type']==request()['task_type']
    client=AgentTaskClient(definition_version='1.1.0',_transport=TaskTransport(_resolver=lambda *x:['93.184.216.34'],_exchange=bridge.task))
    page=client.list_tasks();assert page.tasks[0].task_definition_version=='1.1.0'
    task=client.get_task(result['task_id']);assert task.activity_map_epoch=='isolated-c4'
    from mayim_agent.task_discovery_models import TaskDiscoveryPage
    assert TaskDiscoveryPage.model_validate(bridge.send({'control':'discovery'})).tasks[0].task_definition_digest==task.task_definition_digest
    claim=client.claim_task(task.task_id,agent_id='synthetic-sdk-c3',reward_address=op.payer)
    credential=TaskClaimCredential._from_private_file_payload(claim.credential._to_private_file_payload())
    assert credential.terms_digest==task.terms_digest and credential.activity_map_epoch=='isolated-c4'
    assert 'claim_token' not in json.dumps(claim.model_dump(mode='json'))
    bridge.send(dict(control='epoch',epoch='later-c4'))
    proof=reg.get_registration_challenge(request()['task_type'],op.operation_ref,op.payer).sign(signature)
    recovered=reg.read_registration(proof);assert recovered.result==result
    restored=reg.restore_registration(request(),op._header)
    assert reg.submit_registration(restored)==result
    assert bridge.send({'control':'info'})['counts']['settle']==1
    observed_at=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
    observation=dict(schema_version='ln_church.task_domain_observation_submission.v1',submission_id='sub_'+'6'*32,
        observed_domain='example.com',observed_urls=[dict(url='http://example.com:8080/pay',method='GET',status_code=402,media_family='json',observed_at=observed_at)],
        discovered_surfaces=[dict(url='http://example.com:8080/pay',method='GET',status_code=402,surface_type='unknown',observed_at=observed_at)],errors=[],
        safety_profile='public_safe_light',no_payment_to_target=True,not_a_security_scan=True,
        verification_cost_vector=dict(http_requests=1,tool_calls=1,payment_attempts=0,personal_data_required=False,human_confirmation_required=False,
            irreversible_action_attempted=False,login_attempted=False,form_submission_attempted=False,vulnerability_scan_attempted=False))
    checkpoints=[]
    done=client.submit_and_complete_domain_observation(credential,observation,checkpoint_sink=checkpoints.append)
    assert done.completion_receipt.accepted and checkpoints[-1].credential_fingerprint==credential._local_fingerprint()
    status=client.get_reward_status(task.task_id,submission_id=observation['submission_id'],observation_id=done.register_receipt.observation_id,
        task_definition=credential.task_definition,reward=credential.reward)
    assert status.task_definition_version=='1.1.0'
    resumed=client.submit_and_complete_domain_observation(credential,observation,checkpoint=checkpoints[-1])
    assert resumed.completion_receipt.accepted
    # Closed nested wire and full tuple pins reject alterations; no field is stripped.
    public=task.model_dump(mode='json')
    for key,value in [('activity_map_policy_id','other'),('terms_digest','0'*64),('task_definition_digest',ECONOMIC_PACKS['scheduled']['digest'])]:
        altered=copy.deepcopy(public);altered[key]=value
        with pytest.raises(ValueError):AgentTask.model_validate(altered)
    altered=copy.deepcopy(public);altered['constraints']['arbitrary']=True
    with pytest.raises(ValueError):AgentTask.model_validate(altered)
    original=credential._local_fingerprint();altered=credential._to_private_file_payload();altered['activity_map_epoch']='later-c4'
    assert TaskClaimCredential._from_private_file_payload(altered)._local_fingerprint()!=original
    from mayim_agent.task_transport import TaskTransportError
    before=len(bridge.calls)
    with pytest.raises(TaskTransportError):
        client.submit_and_complete_domain_observation(TaskClaimCredential._from_private_file_payload(altered),observation,checkpoint=checkpoints[-1])
    assert len(bridge.calls)==before

@pytest.mark.parametrize('epoch,status',[(None,503),('different',409)])
def test_new_discovery_conditions_rejected_before_payment(bridge,epoch,status):
    bridge.send(dict(control='epoch',epoch=epoch))
    with pytest.raises(OfferRegistrationError) as e: bridge.client().prepare_registration(request())
    assert e.value.status_code==status
    assert bridge.send({'control':'info'})['counts']['settle']==0

def test_scheduled_registration_canonical_origin_claim_and_recovery(bridge,tmp_path):
    info=bridge.send({'control':'info'})
    when=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime((int(time.time())+10800+899)//900*900))
    metadata=dict(registration_intent_id=str(uuid.uuid4()),task_type='scheduled_http_get_batch.v1',
        task_definition_version='1.2.0',scheduled_at=when,plan_id=info['economics']['plan_id'],
        economic_terms=info['economics'],activity_map_policy_id='mag.activity-map.public.v1',activity_map_epoch='isolated-c4')
    manifest=dict(schema_version='ln_church.http_get_batch_manifest.v1',method='GET',urls=['https://example.com/'])
    reg=bridge.client();quote=reg.prepare_scheduled_registration(metadata,manifest,info['control_key'])
    assert quote.saved_conditions['activity_map_origin']=='https://example.com'
    op=quote.sign(LocalKeyAdapter(SEED));result=reg.submit_scheduled_registration(op)
    terms=bridge.send(dict(control='terms',task_id=result['task_id']))
    validate_economic_wire('scheduled_mag','terms',terms)
    from mayim_agent.task_contract import jcs_canonical_bytes
    assert hashlib.sha256(jcs_canonical_bytes(terms)).hexdigest()==quote.saved_conditions['registration_terms_sha256']
    worker=AgentTaskV2Client(transport=TaskV2Transport(exchange=bridge.scheduled,definition_version='1.2.0'))
    task=worker.get_task(result['task_id']);assert task.activity_map_epoch=='isolated-c4'
    assert worker.list_tasks().tasks[0].task_definition_digest==task.task_definition_digest
    from mayim_agent.task_discovery_models import TaskDiscoveryPage
    assert TaskDiscoveryPage.model_validate(bridge.send({'control':'discovery'})).tasks[0].task_schema_version=='ln_church.agent_task.v2_2'
    credential=worker.claim_task(task.task_id,agent_id='synthetic-sdk-c3',reward_address=info['payer'])
    restored=ScheduledTaskClaimCredential._from_private_file_payload(credential._to_private_file_payload())
    assert restored.terms_digest==task.terms_digest and restored.activity_map_origin=='https://example.com'
    terms_copy=credential.economic_terms;terms_copy['reward']['base_amount_atomic']='999999'
    assert credential.economic_terms['reward']['base_amount_atomic']==info['economics']['reward']['base_amount_atomic']
    bridge.send(dict(control='epoch',epoch='later-c4'))
    assert reg.read_scheduled_registration(op.operation_ref,info['control_key'])['task_id']==task.task_id
    assert reg.submit_scheduled_registration(op,replay=True)==result
    assert bridge.send({'control':'info'})['counts']['settle']==1
    from mayim_agent.task_v2_models import ScheduledCompletionReport
    from mayim_agent.task_journal import TaskJournal
    phase=bridge.send(dict(control='scheduled-time',task_id=task.task_id))
    report=ScheduledCompletionReport.model_validate(dict(schema_version='ln_church.scheduled_http_get_batch_completion.v1',
        submission_id='sub_'+'a'*32,task_type=task.task_type,task_definition_version='1.2.0',task_definition_digest=task.task_definition_digest,
        manifest_sha256=phase['manifest_sha256'],manifest_fetch={'outcome':'release_timeout'},results=[],completed_at=phase['now'].replace('.000Z','Z')))
    journal=TaskJournal(tmp_path/'scheduled.json',task_id=task.task_id,local_claim_credential_handle=credential.local_claim_credential_handle,
        task_type=task.task_type,task_definition_version='1.2.0',task_definition_digest=task.task_definition_digest)
    journal.create();journal.mark_offer_rechecked(phase['manifest_sha256'])
    journal.freeze_report(report.canonical_bytes(),submission_id=report.submission_id,manifest_fetch_outcome='release_timeout')
    ack=worker.complete_task(credential,report,journal=journal)
    assert ack.receipt.task_definition_version=='1.2.0' and ack.receipt.terms_digest==credential.terms_digest
    recovered=worker.recover_completion(restored,report,journal=journal)
    assert recovered.status.terms_digest==credential.terms_digest
    port=bridge.send({'control':'http'})['port']
    private=tmp_path/'credential.json';private.write_text(json.dumps(credential._to_private_file_payload()));private.chmod(0o600)
    output=task_cli(port,'task','run-scheduled-http-get-batch','--credential-file',str(private),'--journal-file',str(tmp_path/'scheduled.json'),'--json')
    assert output['task_id']==task.task_id


def task_cli(port,*args):
    result=subprocess.run([sys.executable,'-I',str(ROOT/'fixtures/mag/task-surface-entry.py'),
        os.environ.get('MAG_INSTALLED_ROOT',str(ROOT.parent)),str(port),*args],capture_output=True,text=True,timeout=25)
    assert result.returncode==0,result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('family',['v17','scheduled'])
def test_actual_cli_claim_persists_new_conditions(bridge,tmp_path,family):
    result=bridge.send(dict(control='seed-registration',profile=family));assert result['status']==201
    info=bridge.send({'control':'info'});port=bridge.send({'control':'http'})['port']
    credential=tmp_path/'claim.json';args=['task','claim' if family=='v17' else 'claim-v2',result['body']['task_id'],
        '--agent-id','synthetic-cli','--reward-address',info['payer'],'--credential-file',str(credential),'--json']
    if family=='scheduled':args+=['--definition-version','1.2.0','--journal-file',str(tmp_path/'journal.json')]
    payload=task_cli(port,*args)
    saved=json.loads(credential.read_text());assert saved['activity_map_epoch']=='isolated-c4'
    assert saved['terms_digest'] and saved['claim_token'] not in json.dumps(payload)
    assert credential.stat().st_mode&0o777==0o600
    if family=='v17':
        listed=task_cli(port,'task','list','--definition-version','1.1.0','--json')
        assert listed['tasks'][0]['task_definition_version']=='1.1.0'


@pytest.mark.parametrize('family',['v17','scheduled'])
def test_original_legacy_registration_recovery_after_epoch_activation(bridge,family):
    bridge.send(dict(control='epoch',epoch=None))
    initial=bridge.send(dict(control='seed-registration',profile=family,legacy=True));assert initial['status']==201
    bridge.send(dict(control='epoch',epoch='isolated-c4'));info=bridge.send({'control':'info'});client=bridge.client()
    if family=='scheduled':
        recovered=client.read_scheduled_registration(initial['saved']['reference'],info['control_key'])
    else:
        challenge=client.get_registration_challenge('payment_surface_discovery.v1',initial['saved']['reference'],info['payer'])
        proof=challenge.sign(lambda typed:bridge.send(dict(control='read-signature',typed_data=typed))['signature'])
        recovered=client.read_registration(proof).result
    assert recovered['task_id']==initial['body']['task_id']
    r=bridge.send(dict(method='POST',path='/api/bazaar/task-offers',headers=initial['saved']['headers'],body=initial['saved']['body']))
    assert r['body']['task_id']==recovered['task_id'] and bridge.send({'control':'info'})['counts']['settle']==1


@pytest.mark.parametrize('kind,surface',[('scheduled','cli'),('discovery','execution')])
def test_actual_new_graph_sources_installed_surfaces_stop_and_unknown_zero(tmp_path,monkeypatch,kind,surface):
    import asyncio
    from test_mag_activity_map_http import Bridge as GraphBridge,client as graph_client
    from test_mag_surface_processes import child
    from mayim_agent.activity_map import ActivityMapError
    monkeypatch.setenv('MAG_C4_SOURCE',kind)
    bridge=GraphBridge('activity-map-producer-http.mjs')
    try:
        now=bridge.command(action='counts')['now']/1000
        monkeypatch.setenv('MAG_SURFACE_CLOCK',str(now))
        bridge.command(action='fault',stopAtPayment=False,failPart=True)
        worker=graph_client(bridge,tmp_path)
        with pytest.raises(ActivityMapError) as error:worker.download(scheme='x402',asset='USDC')
        op=error.value.recovery['operation_id'];bridge.command(action='fault',stopAtPayment=False,failPart=False)
        if surface=='cli':
            r=subprocess.run(child(bridge,'cli','graph','resume','--directory',str(worker.directory),'--operation-id',op),capture_output=True,text=True,timeout=25)
            assert r.returncode==0,r.stderr;payload=json.loads(r.stdout)
        else:
            from mcp import ClientSession,StdioServerParameters
            from mcp.client.stdio import stdio_client
            async def run():
                argv=child(bridge,surface)
                async with stdio_client(StdioServerParameters(command=argv[0],args=argv[1:],env=dict(os.environ))) as streams:
                    async with ClientSession(*streams) as session:
                        await session.initialize();r=await session.call_tool('resume_activity_map',dict(directory=str(worker.directory),operation_id=op))
                        assert not r.isError
                        return r.structuredContent or json.loads(next(x.text for x in r.content if x.type=='text'))
            payload=asyncio.run(asyncio.wait_for(run(),30))
        assert payload['state']=='COMPLETE'
        directory=worker.directory/op/payload['generation'];manifest=json.loads((directory/'manifest.json').read_text())
        assert manifest['source_coverage']['scheduled' if kind=='scheduled' else 'payment_surface_discovery']['state']=='not_confirmed'
        bridge.command(action='stop')
        # A completed purchase is historical local state, not a live-publication
        # assertion. The producer's stopped public generation has a valid zero.
        assert bridge.command(action='counts')['reader_page']['items']==[]
        assert bridge.command(action='counts')['settlements']==1
    finally:bridge.stop()


@pytest.mark.parametrize('kind',['scheduled','discovery'])
def test_new_graph_source_stop_during_acquisition_same_purchase_replacement(tmp_path,monkeypatch,kind):
    from test_mag_activity_map_http import Bridge as GraphBridge,client as graph_client
    monkeypatch.setenv('MAG_C4_SOURCE',kind)
    bridge=GraphBridge('activity-map-producer-http.mjs')
    try:
        worker=graph_client(bridge,tmp_path)
        first=worker.download(scheme='x402',asset='USDC')
        assert first['state']=='REPLACEMENT_REQUIRED'
        replacement=first['replacement']
        empty=worker.replacement(first['operation_id'],decision='accept',generation=replacement['generation'],manifest_sha256=replacement['manifest_sha256'])
        assert empty['state']=='COMPLETE' and empty['expires_at']==first['expires_at']
        assert bridge.command(action='counts')['reader_page']['items']==[]
        assert bridge.command(action='counts')['settlements']==1
    finally:bridge.stop()
