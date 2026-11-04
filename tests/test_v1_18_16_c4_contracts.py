"""Fixed c4 declaration, separate correction, and saved reward invariants."""
import copy
import json
from pathlib import Path
import pytest
from mayim_agent import structured_response_contract as c
from mayim_agent.structured_response_correction import load_correction,DIGEST
from mayim_agent._contract_schema import load_economic_pack
from mayim_agent.task_economic_terms import validate_economic_terms
from mayim_agent.task_discovery_models import _TUPLES


def test_scheduled_11_accepts_both_fixed_schema_asset_spellings_only():
    from mayim_agent.task_v2_models import ScheduledTaskClaimResponse
    value=json.loads((Path(__file__).parent/'fixtures/v1816/scheduled-claim-A.json').read_text())
    value['reward']['asset_address']=value['reward']['asset_address'].lower()
    assert ScheduledTaskClaimResponse.model_validate(value).reward.asset_address==value['reward']['asset_address']
    value['reward']['asset_address']='0x'+'1'*40
    with pytest.raises(ValueError):ScheduledTaskClaimResponse.model_validate(value)


def test_c4_pins_and_original_correction_target():
    # DC 45a64705 fixes Backend c2: all four new tuples are executable.
    added = {('paid_service_trial.v3', '3.2.0'), ('endpoint_choice_reason.v2', '2.4.0'),
             ('immediate_http_visit.v3', '3.2.0'),
             ('structured_response.v3', '3.2.0')}
    assert added <= set(_TUPLES)
    assert len(set(_TUPLES) - added) == 17
    assert _TUPLES[('paid_service_trial.v3', '3.2.0')][1] == '8c44c115574b593274869091d418f4a6e4e086e55893450bf6e91a2b0a6242af'
    assert load_economic_pack('choice')['manifest']['task_definition_digest']=='4c070bbf5fb2c5e1b658f0075fba9e0d6cfd5cb8a48d7191fe027431a72a11df'
    assert c.load_contract_pack('3.1.0')['manifest']['task_definition_digest']=='290fdabf00b6e8d7858af52dff3783032ca71fe58aaed267a1622d831a70ac4c'
    assert c.load_contract_pack()['manifest']['task_definition_digest']=='4d2b8f711b2273fba5215bbcfd767df1145d30a91ee9ea5711437e791616b274'
    assert load_correction()['manifest']['correction_digest']==DIGEST


@pytest.mark.parametrize('scores,expected', [(['0.7'],'25986'),(['0.700000000000000001'],'37123'),(['0.31','0.32'],'11693')])
def test_saved_normal_reward_uses_exact_mean_floor_and_strict_boundary(scores,expected):
    terms={'reward':{'normal_amount_atomic':'37123','compensation_amount_atomic':'92001'},'items':[{'required':True} for _ in scores]}
    status={'quality':{'state':'PASSED','required_values':scores,'amount_atomic':expected},
            'entitlement':{'state':'FINAL','kind':'NORMAL','amount_atomic':expected}}
    c.validate_saved_entitlement(status,terms)
    bad=copy.deepcopy(status);bad['entitlement']['amount_atomic']='150000'
    with pytest.raises(ValueError):c.validate_saved_entitlement(bad,terms)


def test_compensation_keeps_saved_amount_and_pending_has_no_amount():
    terms={'reward':{'compensation_amount_atomic':'92001'}}
    value={'quality':{'state':'UNAVAILABLE'},'entitlement':{'state':'FINAL','kind':'COMPENSATION','amount_atomic':'92001'}}
    c.validate_saved_entitlement(value,terms)
    value['entitlement']['amount_atomic']='150000'
    with pytest.raises(ValueError):c.validate_saved_entitlement(value,terms)
    value['entitlement']={'state':'PENDING','amount_atomic':None}
    c.validate_saved_entitlement(value,terms)
    value['entitlement']['amount_atomic']='0'
    with pytest.raises(ValueError):c.validate_saved_entitlement(value,terms)


def test_structured_zero_base_fee_and_opaque_current_conditions():
    terms=dict(economic_terms_revision='future.opaque~B',plan_id='pure-label',capacity_total=7,
        registration_amount_atomic='9876543',base_fee_atomic='0',unfilled_refund_each_atomic='123456',
        reward=dict(normal_amount_atomic='123456',compensation_amount_atomic='6543'))
    assert validate_economic_terms('structured',terms)==terms
