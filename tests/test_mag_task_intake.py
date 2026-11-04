"""Fixed-byte intake and synthetic SDK contract checks; not producer E2E."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from mayim_agent._contract_schema import load_economic_pack, validate
from mayim_agent.task_contract import jcs_canonical_bytes
from mayim_agent.immediate_visit_models import ImmediateVisitTask, ImmediateVisitClaimCredential
from mayim_agent.endpoint_choice_reason_models import EndpointChoiceClaimCredential

ROOT=Path(__file__).parent/'fixtures'
POLICY=dict(activity_map_policy_id='mag.activity-map.public.v1',activity_map_epoch='synthetic-C')


def digest(value):return hashlib.sha256(jcs_canonical_bytes(value)).hexdigest()


@pytest.mark.parametrize('family',['immediate','paid','choice','structured'])
def test_fixed_pack_identity_and_all_entry_hashes(family):
    assert load_economic_pack(family+'_mag')['manifest']['descriptor']['activity_map_policy_id']==POLICY['activity_map_policy_id']


def immediate():
    task=json.loads((ROOT/'v1816/immediate-task-A.json').read_text())
    claim=json.loads((ROOT/'v1816/immediate-claim-A.json').read_text())
    pin=load_economic_pack('immediate_mag')['manifest']['task_definition_digest']
    terms=claim['terms_snapshot'];terms.update(POLICY,definition_version='3.2.0',task_definition_digest=pin)
    claim.update(schema_version=claim['schema_version'].replace('v3_1','v3_2'),terms_digest=digest(terms))
    task.update(POLICY,definition_version='3.2.0',terms_digest=digest(terms),schema_version=task['schema_version'].replace('v3_1','v3_2'))
    task['definition_url']=task['definition_url'].replace('3.1.0','3.2.0')
    return task,claim


def choice():
    claim=json.loads((ROOT/'v1816-c4/choice-claim-A.json').read_text())
    pin=load_economic_pack('choice_mag')['manifest']['task_definition_digest']
    claim['terms_snapshot'].update(POLICY,task_definition_version='2.4.0',task_definition_digest=pin)
    claim.update(schema_version=claim['schema_version'].replace('v2_3','v2_4'),task_definition_version='2.4.0',
                 task_definition_digest=pin,terms_digest=digest(claim['terms_snapshot']))
    return claim


def test_immediate_new_task_claim_roundtrip():
    t,p=immediate();task=ImmediateVisitTask.model_validate(t);claim=ImmediateVisitClaimCredential.model_validate(p)
    assert task.terms_digest==claim.terms_digest
    assert ImmediateVisitClaimCredential.model_validate(claim._private_payload()).terms_snapshot['activity_map_epoch']=='synthetic-C'


def test_choice_new_claim_roundtrip():
    p=choice();claim=EndpointChoiceClaimCredential.model_validate(p)
    assert EndpointChoiceClaimCredential.model_validate(claim).terms_snapshot['activity_map_epoch']=='synthetic-C'


@pytest.mark.parametrize('family',['immediate','choice'])
@pytest.mark.parametrize('mutation',['missing','policy','epoch','digest','unknown_tuple'])
def test_new_snapshot_tamper_rejected(family,mutation):
    p=immediate()[1] if family=='immediate' else choice()
    model=ImmediateVisitClaimCredential if family=='immediate' else EndpointChoiceClaimCredential
    if mutation=='missing':p['terms_snapshot'].pop('activity_map_policy_id')
    if mutation=='policy':p['terms_snapshot']['activity_map_policy_id']='invented'
    if mutation=='epoch':p['terms_snapshot']['activity_map_epoch']='another-C'
    if mutation=='digest':p['terms_digest']='0'*64
    if mutation=='unknown_tuple':p['schema_version']=p['schema_version'].rsplit('.',1)[0]+'.v99'
    with pytest.raises(ValueError):model.model_validate(p)


def test_structured_new_task_and_claim():
    from test_v1_18_14_structured_response_prepack import task,claim
    from mayim_agent.structured_response_contract import terms_snapshot
    from mayim_agent.structured_response_models import StructuredResponseTask,StructuredResponseClaimCredential
    t=task();pin=load_economic_pack('structured_mag')['manifest']['task_definition_digest']
    t.update(POLICY,schema_version='ln_church.agent_task.structured_response.v3_2',task_definition_version='3.2.0',
        task_definition_digest=pin,economic_terms_revision='synthetic-A',unfilled_refund_each_atomic='150000')
    t['manifest_url']=t['manifest_url'].replace('3.0.0','3.2.0');t['definition_url']=t['definition_url'].replace('3.0.0','3.2.0')
    t['terms_digest']=digest(terms_snapshot(t))
    assert StructuredResponseTask(t).supported
    p=claim(t);p.update(schema_version='ln_church.agent_task_claim_response.structured_response.v3_2',task_definition_version='3.2.0',
        task_definition_digest=pin,terms_digest=t['terms_digest'],economic_terms_revision='synthetic-A',terms_snapshot=terms_snapshot(t))
    assert StructuredResponseClaimCredential(p).terms_digest==t['terms_digest']
