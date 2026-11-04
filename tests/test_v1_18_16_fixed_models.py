"""Fixed DTO parsing: retained c2 unchanged families and actual c4 Choice DTOs.

Historical c2 Choice files remain evidence and must fail the c4 definition pin.
These unit tests are separate from the c4 HTTP integration lane.
"""
import copy
import json
from pathlib import Path
import pytest
from mayim_agent._contract_schema import load_economic_pack
from mayim_agent.immediate_visit_models import ImmediateVisitTask,ImmediateVisitClaimCredential,ImmediateVisitSubmissionStatus
from mayim_agent.paid_service_trial_models import model_for
from mayim_agent.endpoint_choice_reason_models import EndpointChoiceClaimCredential,EndpointChoiceSubmissionStatus
from mayim_agent.task_v2_models import ScheduledHttpGetBatchTask,ScheduledTaskClaimResponse,ScheduledTaskClaimCredential,ScheduledCompletionReceipt,ScheduledRewardStatus,ScheduledCompletionAcknowledgement
ROOT=Path(__file__).parent/'fixtures/v1816'
def dto(name):
 root = ROOT.parent/'v1816-c4' if name.startswith('choice-') else ROOT
 return json.loads((root/(name+'.json')).read_text())

@pytest.mark.parametrize('rev',['A','B'])
def test_historical_choice_candidate_is_not_current_c4(rev):
 p=json.loads((ROOT/('choice-claim-'+rev+'.json')).read_text())
 with pytest.raises(ValueError):EndpointChoiceClaimCredential.model_validate(p)

@pytest.mark.parametrize('family',['scheduled','immediate','paid','choice'])
def test_new_pack_pins(family):assert load_economic_pack(family)['manifest']['task_definition_digest']

@pytest.mark.parametrize('rev',['A','B'])
def test_scheduled_private_snapshot_and_compound_ack(rev):
 task=ScheduledHttpGetBatchTask.model_validate(dto('scheduled-task-'+rev));response=ScheduledTaskClaimResponse.model_validate(dto('scheduled-claim-'+rev));credential=response.to_credential(agent_id='synthetic')
 saved=credential._to_private_file_payload();restored=ScheduledTaskClaimCredential._from_private_file_payload(saved)
 assert restored.economic_terms==credential.economic_terms and restored.terms_digest==credential.terms_digest
 assert task.reward['base_amount_atomic']==credential.reward.amount_atomic
 receipt=ScheduledCompletionReceipt.model_validate(dto('scheduled-receipt-'+rev));status=ScheduledRewardStatus.model_validate(dto('scheduled-status-'+rev))
 assert receipt.economic_terms_revision==status.economic_terms_revision==credential.economic_terms_revision
 for k,value in [('receipt',receipt),('status',status)]:
  a=ScheduledCompletionAcknowledgement(source=k,**{k:value});p=a.model_dump(mode='json',exclude_none=True)
  assert p[k]['economic_terms_revision']==credential.economic_terms_revision

@pytest.mark.parametrize('rev',['A','B'])
def test_connected_family_models_and_private_round_trip(rev):
 for task_model,claim_model,status_model,prefix,status in [(ImmediateVisitTask,ImmediateVisitClaimCredential,ImmediateVisitSubmissionStatus,'immediate','immediate-status-immediate-'+rev),(model_for('task','v3_1'),model_for('claim','v3_1'),model_for('status','v3_1'),'paid','paid-status-paid-'+rev)]:
  task=task_model.model_validate(dto(prefix+('-task-get-' if prefix=='paid' else '-task-')+rev));claim=claim_model.model_validate(dto(prefix+'-claim-'+rev));st=status_model.model_validate(dto(status))
  assert claim_model.model_validate(claim._private_payload()).terms_digest==claim.terms_digest
  assert task.reward==claim.reward
 claim=EndpointChoiceClaimCredential.model_validate(dto('choice-claim-'+rev));assert EndpointChoiceClaimCredential.model_validate(claim).terms_snapshot==claim.terms_snapshot
 EndpointChoiceSubmissionStatus.model_validate(dto('choice-status-'+('A-after-B' if rev=='A' else 'B')))

@pytest.mark.parametrize('family,model', [('immediate',ImmediateVisitClaimCredential),('paid',model_for('claim','v3_1')),('choice',EndpointChoiceClaimCredential)])
@pytest.mark.parametrize('mutation',['missing','revision','digest','snapshot','extra','old_definition'])
def test_claim_snapshot_tampering_rejected(family,model,mutation):
 p=dto(family+'-claim-B')
 if mutation=='missing':p.pop('terms_snapshot')
 elif mutation=='revision':p['economic_terms_revision']='different-revision'
 elif mutation=='digest':p['terms_digest']='0'*64
 elif mutation=='snapshot':p['terms_snapshot']['capacity_total']+=1
 elif mutation=='extra':p['terms_snapshot']['unknown_capability']=True
 else:p['task_definition_digest']='0'*64
 with pytest.raises(ValueError):model.model_validate(p)

@pytest.mark.parametrize('amount',['00','-1','1.0',str(2**256)])
def test_scheduled_rejects_invalid_saved_amount(amount):
 p=dto('scheduled-claim-B');p['economic_terms']['reward']['base_amount_atomic']=amount;p['reward']['amount_atomic']=amount
 with pytest.raises(ValueError):ScheduledTaskClaimResponse.model_validate(p)


def test_current_offer_zero_bonus_is_legal_but_base_zero_is_not():
 from mayim_agent.task_economic_terms import validate_economic_terms
 p=dto('immediate-request-B')['economic_terms'];p['reward']['bonus_amount_atomic']='0';p['reward']['maximum_amount_atomic']=p['reward']['base_amount_atomic']
 assert validate_economic_terms('immediate',p)==p
 p['reward']['base_amount_atomic']='0'
 with pytest.raises(ValueError):validate_economic_terms('immediate',p)
