"""DC c2 Paid producer connection; no live provider or real assets."""
import os,copy,json
import pytest
from test_mag_task_http import Bridge,NETWORK
pytestmark=pytest.mark.skipif(not os.environ.get('MAG_BACKEND_C2_ROOT'),reason='Fixed Backend c2 required')
@pytest.fixture
def bridge():
 b=Bridge()
 try:yield b
 finally:b.close()

def test_paid_real_A_B_saved_purchase_and_journal(bridge,tmp_path):
 from mayim_agent.paid_service_trial_client import PaidServiceTrialTaskClient
 from mayim_agent.paid_service_trial_transport import PaidServiceTrialTransport,PaidServiceTrialRawResponse
 from mayim_agent.paid_service_trial_models import FrozenPaidServiceTrialReport
 from mayim_agent.paid_service_trial_journal import PaidServiceTrialJournal
 tmp_path.chmod(0o700)
 def exchange(*a,**kw):
  r=bridge(*a,**kw);return PaidServiceTrialRawResponse(r.status_code,r.headers,r.body)
 worker=PaidServiceTrialTaskClient(version='v3_2',transport=PaidServiceTrialTransport(version='v3_2',exchange=exchange),claim_directory=tmp_path)
 saved=[]
 for rev in ('A','B'):
  terms=dict(economic_terms_revision='paid-'+rev,plan_id='label-'+rev,registration_amount_atomic='1000000' if rev=='A' else '2000000',capacity_total=5 if rev=='A' else 2,reward=dict(NETWORK,amount_atomic='20000' if rev=='A' else '30000'))
  bridge.send(dict(control='economic',family='paid',terms=terms));r=bridge.send(dict(control='paid-seed',rev=rev,terms=terms,version='3.2.0'))
  from mayim_agent.offer_registration import _paid_read_success
  reg=r['registration']
  assert _paid_read_success(reg,reg['registration_intent_id'])['task_id']==r['task_id']
  from mayim_agent.task_discovery_models import TaskDiscoveryPage
  page=TaskDiscoveryPage.model_validate(bridge.send({'control':'discovery'}))
  row=next(t for t in page.tasks if t.task_id==r['task_id'])
  assert row.task_definition_version=='3.2.0'
  assert row.task_definition_digest=='8c44c115574b593274869091d418f4a6e4e086e55893450bf6e91a2b0a6242af'
  task=worker.get_task(r['task_id']);claim=worker.claim_task(task.task_id,'synthetic','0x'+'0'*39+'3',idempotency_key='paid-'+rev)
  assert claim.reward.amount_atomic==terms['reward']['amount_atomic']
  assert claim.terms_snapshot['activity_map_policy_id']=='mag.activity-map.public.v1'
  assert claim.terms_snapshot['activity_map_epoch']=='synthetic-C'
  assert task.terms_digest==claim.terms_digest
  from mayim_agent.paid_service_trial_models import parse_claim
  for mutation in ('missing','unknown','epoch','digest','unknown_tuple'):
   invalid=copy.deepcopy(claim._private_payload())
   if mutation=='missing':invalid['terms_snapshot'].pop('activity_map_policy_id')
   if mutation=='unknown':invalid['terms_snapshot']['unexpected']=True
   if mutation=='epoch':invalid['terms_snapshot']['activity_map_epoch']='different-epoch'
   if mutation=='digest':invalid['terms_digest']='0'*64
   if mutation=='unknown_tuple':invalid['task_definition_version']='3.99.0'
   with pytest.raises(ValueError):parse_claim(invalid)
  conflict=bridge('POST','/api/agent/tasks/'+task.task_id+'/claim',None,{'Idempotency-Key':'paid-'+rev,'Content-Type':'application/json'},json.dumps(dict(schema_version='ln_church.agent_task_claim_request.v1',agent_id='different',reward_address=claim.reward_address)).encode())
  assert conflict.status_code==409
  assert worker.recover_claim(task.task_id,idempotency_key='paid-'+rev).execution_id==claim.execution_id
  data=dict(schema_version='ln_church.task_completion.paid_service_trial.v3_2',task_id=claim.task_id,task_type=claim.task_type,task_definition_version=claim.task_definition_version,task_definition_digest=claim.task_definition_digest,terms_digest=claim.terms_digest,request_digest=claim.request_digest,purchase_terms_digest=claim.purchase_terms_digest,execution_id=claim.execution_id,submission_id='sub_'+('e' if rev=='A' else 'f')*32,purchase=r['purchase'])
  report=FrozenPaidServiceTrialReport.from_dict(data)
  bad=copy.deepcopy(data);bad['purchase']['amount']='20000'
  with pytest.raises(ValueError):FrozenPaidServiceTrialReport.from_dict(bad).model.require_claim(claim)
  journal=PaidServiceTrialJournal(tmp_path,claim);journal.prepare(report,'a'*64);journal.reserve_paid_dispatch();journal.save_outcome('RESPONSE_RECEIVED',200,None)
  worker.complete_task(claim,report,journal=journal);saved.append((claim,report,terms,r['purchase'],rev))
 for claim,report,terms,purchase,rev in reversed(saved):
  bridge.send(dict(control='paid-evaluate',task=claim.task_id,execution=claim.execution_id,purchase=purchase,rev=rev))
  status=worker.get_submission_status(claim,report);assert status.evaluation.approved_amount_atomic==terms['reward']['amount_atomic']
  recovered=PaidServiceTrialJournal.load_claim(tmp_path,claim.task_id,claim.execution_id);assert recovered.terms_snapshot==claim.terms_snapshot
  before=len(bridge.calls);worker.recover_completion(recovered,report,journal=PaidServiceTrialJournal(tmp_path,recovered));assert all(m=='GET' for m,_ in bridge.calls[before:])



@pytest.mark.parametrize('lose_response',[False,True])
def test_paid_c2_real_executor_keeps_one_purchase(bridge,tmp_path,lose_response):
 from types import SimpleNamespace
 import base64
 from mayim_agent.crypto.evm import LocalKeyAdapter
 from mayim_agent.models import PaymentPolicy
 from mayim_agent.paid_service_trial import PaidServiceTrialExecutor
 from mayim_agent.paid_service_trial_client import PaidServiceTrialTaskClient
 from mayim_agent.paid_service_trial_transport import PaidServiceTrialTransport,PaidServiceTrialRawResponse
 from mayim_agent.paid_service_trial_journal import PaidServiceTrialJournal
 from mayim_agent.paid_service_trial_network import PaidTrialHTTPResponse
 signer=LocalKeyAdapter('17'*32)  # Public synthetic signer; never broadcast.
 terms=dict(economic_terms_revision='paid-A',plan_id='label-A',registration_amount_atomic='1000000',capacity_total=5,reward=dict(NETWORK,amount_atomic='20000'))
 bridge.send(dict(control='economic',family='paid',terms=terms))
 seed=bridge.send(dict(control='paid-seed',rev='A',terms=terms,version='3.2.0'))
 def exchange(*args,**kwargs):
  r=bridge(*args,**kwargs);return PaidServiceTrialRawResponse(r.status_code,r.headers,r.body)
 tmp_path.chmod(0o700)
 worker=PaidServiceTrialTaskClient(version='v3_2',transport=PaidServiceTrialTransport(version='v3_2',exchange=exchange),claim_directory=tmp_path)
 claim=worker.claim_task(seed['task_id'],'synthetic',signer.address,idempotency_key='executor-A')
 class Seller:
  paid=0
  def fetch(self,request,*,payment_signature=None):
   assert request==claim.request.model_dump(mode='json')
   if payment_signature is None:
    challenge={'x402Version':2,'resource':{'url':request['url']},'accepts':[claim.purchase_terms.requirements.wire()]}
    return PaidTrialHTTPResponse(402,[('PAYMENT-REQUIRED',base64.b64encode(json.dumps(challenge).encode()).decode())],b'')
   self.paid+=1
   if lose_response:raise TimeoutError('synthetic lost seller response')
   return PaidTrialHTTPResponse(200,[],b'synthetic private product')
 seller=Seller();journal=PaidServiceTrialJournal(tmp_path,claim)
 executor=PaidServiceTrialExecutor(signer=signer,policy=PaymentPolicy(max_spend_per_tx_usd=.02,max_spend_per_session_usd=.02,allowed_hosts=['seller.example']),client=worker,http=seller,block_guard=SimpleNamespace(ready=lambda value:True),wall_time=lambda:bridge.send({'control':'counts'})['now']/1000)
 result=executor.execute(claim,journal=journal)
 assert result.state=='REPORTED' and seller.paid==1
 assert result.report.model.task_definition_version=='3.2.0'
 recovered=PaidServiceTrialJournal.load_claim(tmp_path,claim.task_id,claim.execution_id)
 second=executor.execute(recovered,journal=PaidServiceTrialJournal(tmp_path,recovered))
 assert second.state=='REPORTED' and seller.paid==1
 assert second.report.to_bytes()==result.report.to_bytes()
 assert 'synthetic private product' not in journal.path.read_text()
