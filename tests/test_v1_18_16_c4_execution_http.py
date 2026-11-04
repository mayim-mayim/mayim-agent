"""Fixed c4 real execution owners, with declared asset-free external doubles."""
import json,subprocess,os,urllib.request,urllib.error,copy
from pathlib import Path
import pytest
from mayim_agent.immediate_visit_transport import ImmediateVisitRawResponse,ImmediateVisitTransport
from mayim_agent.offer_registration import OfferRegistrationClient,_request,_success
from mayim_agent.crypto.evm import LocalKeyAdapter
from mayim_agent._contract_schema import validate_economic_wire
from mayim_agent.task_economic_terms import validate_economic_terms,validate_offers
from mayim_agent.immediate_visit_client import AgentImmediateVisitClient
from mayim_agent.immediate_visit_models import FrozenImmediateVisitReport,ImmediateVisitCompletionReport
from mayim_agent.immediate_visit_journal import ImmediateVisitJournal
from mayim_agent.endpoint_choice_reason_client import AgentEndpointChoiceReasonClient
from mayim_agent.endpoint_choice_reason_journal import EndpointChoiceJournal
from mayim_agent.endpoint_choice_reason_transport import EndpointChoiceTransport

ROOT=Path(__file__).resolve().parents[1]
BACKEND=os.environ.get('LN_CHURCH_C4_ROOT')
pytestmark=pytest.mark.skipif(not BACKEND,reason='Fixed Backend c4 checkout is required')
NETWORK=dict(network='eip155:8453',asset='USDC',asset_address='0x833589fcd6edb6e08f4c7c32d4f71b54bda02913')
def economics(family,rev):
 return dict(economic_terms_revision=family+'-'+rev,plan_id='label-'+rev,registration_amount_atomic='1000000' if rev=='A' else '2000000',capacity_total=5 if rev=='A' else 2,**({'creator_amount_atomic':'0'} if family=='choice' else {}),reward=dict(NETWORK,**(dict(correct_amount_atomic='25000' if rev=='A' else '30000',incorrect_amount_atomic='10000' if rev=='A' else '11000') if family=='choice' else dict(base_amount_atomic='7500' if rev=='A' else '9000',bonus_amount_atomic='7500' if rev=='A' else '1000',maximum_amount_atomic='15000' if rev=='A' else '10000'))))
class Bridge:
 def __init__(self):
  self.stderr=open(os.devnull,'w')
  self.proc=subprocess.Popen(['node',str(ROOT/'tests/fixtures/v1816-c4/execution-http.mjs')],env=dict(os.environ,HONDO_ROOT=BACKEND,AWS_EC2_METADATA_DISABLED='true'),stdout=subprocess.PIPE,stderr=self.stderr,text=True)
  line=self.proc.stdout.readline();assert line,'Bridge failed to start';self.port=json.loads(line)['port'];self.calls=[]
 def send(self,x):
  try:
   with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:'+str(self.port)+'/_test/control',data=json.dumps(x).encode(),headers={'Content-Type':'application/json'}),timeout=20) as f:r=json.load(f)
  except urllib.error.HTTPError as e:raise AssertionError(e.read().decode())
  return r
 def __call__(self,method,path,query,headers,body,timeout=20,**kwargs):
  self.calls.append((method,path))
  try:
   r=urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:'+str(self.port)+path+('?' + query if query else ''),method=method,headers=dict(headers),data=body if method!='GET' else None),timeout=timeout)
  except urllib.error.HTTPError as e:r=e
  with r:return ImmediateVisitRawResponse(r.status,dict(r.headers),r.read())
 def close(self):self.proc.terminate();self.proc.wait(timeout=5);self.stderr.close()
@pytest.fixture
def bridge():
 b=Bridge()
 try:yield b
 finally:b.close()

def register(b,family,rev):
 e=economics(family,rev);b.send(dict(control='economic',family=family,terms=e))
 if family=='choice':r=dict(schema_version='ln_church.task_offer_registration.endpoint_choice_reason.v2_3',task_type='endpoint_choice_reason.v2',task_definition_version='2.3.0',plan_id=e['plan_id'],economic_terms=e,question='Synthetic question',candidates=[dict(candidate_id='a',url='https://example.com/')],correct_candidate_ids=['a'],reveal_correct_set_after_answer=False)
 else:r=dict(schema_version='ln_church.task_offer_create_request.immediate_visit.v3_1',task_type='immediate_http_visit.v3',task_definition_version='3.1.0',plan_id=e['plan_id'],economic_terms=e,repeat_policy='allow',urls=['https://example.com/'])
 client=OfferRegistrationClient(transport=ImmediateVisitTransport(exchange=b,version='v3_1'),wall_time=lambda:b.send({'control':'counts'})['now']/1000)
 quote=client.prepare_registration(r);operation=quote.sign(LocalKeyAdapter('17'*32),now=b.send({'control':'counts'})['now']/1000)
 result=client.submit_registration(operation)
 assert result['economic_terms_revision']==e['economic_terms_revision']
 assert result['registration_amount_atomic']==e['registration_amount_atomic']
 return e,result,operation,client

def test_choice_real_A_B_and_journal(bridge,tmp_path):
 tmp_path.chmod(0o700);saved=[]
 worker=AgentEndpointChoiceReasonClient(version='v2_3',transport=EndpointChoiceTransport(exchange=bridge,version='v2_3'))
 for rev in ['A','B']:
  e,r,op,requester=register(bridge,'choice',rev)
  task=worker.get_task(r['task_id']);assert task.reward.correct_amount_atomic==e['reward']['correct_amount_atomic']
  j=EndpointChoiceJournal(tmp_path,task_id=task.task_id,agent_id='synthetic',reward_address=op.payer,idempotency_key='sdk-'+rev,version='v2_3')
  claim=worker.claim_task(journal=j)
  if os.environ.get('LN_C4_CAPTURE_DTOS'):
   path=Path(os.environ['LN_C4_CAPTURE_DTOS']);path.mkdir(parents=True,exist_ok=True)
   public=claim._private_payload();public['claim_token']='A'*43
   (path/('choice-claim-'+rev+'.json')).write_text(json.dumps(public,indent=2)+'\n')
  conflict=bridge('POST','/api/agent/tasks/'+task.task_id+'/claim',None,{'Idempotency-Key':'sdk-'+rev,'Content-Type':'application/json'},json.dumps(dict(schema_version='ln_church.agent_task_claim_request.v1',agent_id='different',reward_address=op.payer)).encode())
  assert conflict.status_code==409
  assert worker.recover_claim(journal=j).execution_id==claim.execution_id
  report=worker.prepare_answer(journal=j,selected_candidate_id='a',answer_reason='Synthetic answer',submission_id='sub_'+('a' if rev=='A' else 'b')*32)
  worker.complete_task(journal=j);saved.append((j,claim,e,r))
 for j,claim,e,r in reversed(saved):
  bridge.send(dict(control='evaluate',task=claim.task_id,execution=claim.execution_id,q='1'))
  status=worker.get_submission_status(journal=j);assert status.evaluation.approved_amount_atomic==e['reward']['correct_amount_atomic']
  if os.environ.get('LN_C4_CAPTURE_DTOS'):
   (Path(os.environ['LN_C4_CAPTURE_DTOS'])/('choice-status-'+('A-after-B' if e['economic_terms_revision']=='choice-A' else 'B')+'.json')).write_text(json.dumps(dict(status.model_dump(),answer_reason=status.answer_reason,correct_candidate_ids=status.correct_candidate_ids),indent=2)+'\n')
  reopened=EndpointChoiceJournal(tmp_path,task_id=claim.task_id,agent_id='synthetic',reward_address=claim.reward_address,idempotency_key=j.key,version='v2_3')
  before=len(bridge.calls);worker.recover_completion(journal=reopened);assert all(method=='GET' for method,_ in bridge.calls[before:])
  # c4 uses the same closed result inside the COMMITTED envelope.
  envelope=dict(schema_version='ln_church.registration_read_result.v1',profile='V189_ENDPOINT_CHOICE_REASON',operation_ref=r['registration_intent_id'],kind='COMMITTED',result=r,status=None)
  validate_economic_wire('choice','registration_result',r)
  validate_economic_wire('choice','registration_recovery_result',envelope)
 assert bridge.send({'control':'counts'})['transactions']>0

def test_immediate_real_A_B_and_journal(bridge,tmp_path):
 tmp_path.chmod(0o700);saved=[]
 worker=AgentImmediateVisitClient(version='v3_1',transport=ImmediateVisitTransport(exchange=bridge,version='v3_1'))
 for rev in ['A','B']:
  e,r,op,_=register(bridge,'immediate',rev);task=worker.get_task(r['task_id'])
  claim=worker.claim_task(task.task_id,'synthetic',op.payer,idempotency_key='sdk-'+rev)
  j=ImmediateVisitJournal(tmp_path,claim)
  obs=bridge.send({'control':'immediate-observation'})
  report=FrozenImmediateVisitReport.from_report(ImmediateVisitCompletionReport(schema_version='ln_church.task_completion.immediate_visit.v3_1',task_id=claim.task_id,execution_id=claim.execution_id,submission_id='sub_'+('c' if rev=='A' else 'd')*32,endpoint_id=claim.endpoints[0].endpoint_id,profile_id=claim.profile_id,observation=obs))
  j.prepare_report(claim,report);worker.complete_task(claim,report,journal=j);saved.append((j,claim,report,e))
 for j,claim,report,e in reversed(saved):
  bridge.send(dict(control='immediate-evaluate',task=claim.task_id,execution=claim.execution_id))
  status=worker.get_submission_status(claim,report);assert status.approved_amount_atomic==e['reward']['maximum_amount_atomic']
  recovered=ImmediateVisitJournal.load_claim(tmp_path,claim.task_id,claim.execution_id);assert recovered.terms_snapshot==claim.terms_snapshot
  before=len(bridge.calls);worker.recover_completion(recovered,report,journal=ImmediateVisitJournal(tmp_path,recovered));assert all(m=='GET' for m,_ in bridge.calls[before:])

def test_current_offers_are_closed_and_do_not_change_A(bridge):
 for family in ('choice','immediate'):
  e,r,op,client=register(bridge,family,'A')
  a=client.get_economic_offers(family);assert a['offers'][0]['economic_terms_revision']==family+'-A'
  bridge.send(dict(control='economic',family=family,terms=economics(family,'B')))
  b=client.get_economic_offers(family);assert b['offers'][0]['economic_terms_revision']==family+'-B'
  assert r['economic_terms_revision']==family+'-A'
  assert a['offers'][0]['registration_amount_atomic']=='1000000'
  with pytest.raises(ValueError):validate_economic_terms(family,a['offers'][0])

def test_paid_real_A_B_saved_purchase_and_journal(bridge,tmp_path):
 from mayim_agent.paid_service_trial_client import PaidServiceTrialTaskClient
 from mayim_agent.paid_service_trial_transport import PaidServiceTrialTransport,PaidServiceTrialRawResponse
 from mayim_agent.paid_service_trial_models import FrozenPaidServiceTrialReport
 from mayim_agent.paid_service_trial_journal import PaidServiceTrialJournal
 tmp_path.chmod(0o700)
 def exchange(*a,**kw):
  r=bridge(*a,**kw);return PaidServiceTrialRawResponse(r.status_code,r.headers,r.body)
 worker=PaidServiceTrialTaskClient(version='v3_1',transport=PaidServiceTrialTransport(version='v3_1',exchange=exchange),claim_directory=tmp_path)
 saved=[]
 for rev in ('A','B'):
  terms=dict(economic_terms_revision='paid-'+rev,plan_id='label-'+rev,registration_amount_atomic='1000000' if rev=='A' else '2000000',capacity_total=5 if rev=='A' else 2,reward=dict(NETWORK,amount_atomic='20000' if rev=='A' else '30000'))
  bridge.send(dict(control='economic',family='paid',terms=terms));r=bridge.send(dict(control='paid-seed',rev=rev,terms=terms))
  task=worker.get_task(r['task_id']);claim=worker.claim_task(task.task_id,'synthetic','0x'+'0'*39+'3',idempotency_key='paid-'+rev)
  assert claim.reward.amount_atomic==terms['reward']['amount_atomic']
  conflict=bridge('POST','/api/agent/tasks/'+task.task_id+'/claim',None,{'Idempotency-Key':'paid-'+rev,'Content-Type':'application/json'},json.dumps(dict(schema_version='ln_church.agent_task_claim_request.v1',agent_id='different',reward_address=claim.reward_address)).encode())
  assert conflict.status_code==409
  assert worker.recover_claim(task.task_id,idempotency_key='paid-'+rev).execution_id==claim.execution_id
  data=dict(schema_version='ln_church.task_completion.paid_service_trial.v3_1',task_id=claim.task_id,task_type=claim.task_type,task_definition_version=claim.task_definition_version,task_definition_digest=claim.task_definition_digest,terms_digest=claim.terms_digest,request_digest=claim.request_digest,purchase_terms_digest=claim.purchase_terms_digest,execution_id=claim.execution_id,submission_id='sub_'+('e' if rev=='A' else 'f')*32,purchase=r['purchase'])
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

def test_new_immediate_409_and_original_claim_recovery(bridge):
 from mayim_agent.immediate_visit_transport import ImmediateVisitAPIError
 e,r,op,_=register(bridge,'immediate','A');worker=AgentImmediateVisitClient(version='v3_1',transport=ImmediateVisitTransport(exchange=bridge,version='v3_1'))
 claim=worker.claim_task(r['task_id'],'synthetic',op.payer,idempotency_key='same-key')
 with pytest.raises(ImmediateVisitAPIError) as exc:worker.claim_task(r['task_id'],'different',op.payer,idempotency_key='same-key')
 assert exc.value.status_code==409
 restored=worker.recover_claim(r['task_id'],'synthetic',op.payer,idempotency_key='same-key')
 assert restored.execution_id==claim.execution_id and restored.terms_snapshot==claim.terms_snapshot
