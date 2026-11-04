"""DC T-INT-C1-SD01-02: exact real-producer status and bounded fault variants."""
import copy
import json
from pathlib import Path
import pytest
from jsonschema import Draft202012Validator, FormatChecker
from mayim_agent.task_client import AgentTaskClient
from mayim_agent.task_transport import TaskTransport, TaskTransportError
from mayim_agent.task_models import (AgentTaskRewardStatus, TaskDefinitionReference,
    AgentTaskRewardTerms, TaskDomainObservationGuidedResult, verify_reward_status_transition)

CAPTURES = Path(__file__).parent / 'evidence/mag-sdk-c4/captures'
def capture(name='new-status-approved'):
    return json.loads((CAPTURES / (name+'.json')).read_text())

def client_for(wire):
    calls=[]
    def exchange(**kw):
        calls.append(kw)
        return 200, {'content-type':'application/json'}, json.dumps(wire).encode()
    client=AgentTaskClient(definition_version='1.1.0', _transport=TaskTransport(
        _resolver=lambda *args: ['93.184.216.34'], _exchange=exchange))
    return client,calls

def arguments(base):
    return dict(task_id=base['task_id'],submission_id=base['submission_id'],observation_id=base['observation_id'],
        task_definition=TaskDefinitionReference(**{k:base[k] for k in TaskDefinitionReference.model_fields}),
        reward=AgentTaskRewardTerms(**{k:base[k] for k in ('network','asset','asset_address','amount_atomic')}))

@pytest.mark.parametrize('name',['new-status-approved','new-status-pending'])
@pytest.mark.parametrize('method',['get_reward_status','wait_for_reward'])
def test_real_producer_status_preserves_every_field(name,method):
    wire=capture(name);client,calls=client_for(wire)
    args=arguments(wire)
    if method=='wait_for_reward':args['max_attempts']=1
    result=getattr(client,method)(**args)
    assert result.model_dump(mode='json')==wire
    assert result.evaluated_at==wire['evaluated_at']
    assert len(calls)==1 and calls[0]['method']=='GET'

@pytest.mark.parametrize('mutation', ['integer','missing','extra','date','null','reward_state','status_semantics'])
@pytest.mark.parametrize('method',['get_reward_status','wait_for_reward'])
def test_fixed_schema_violations_are_response_invalid(mutation,method):
    base=capture();wire=copy.deepcopy(base)
    if mutation=='integer':wire['evaluated_at']=42
    elif mutation=='missing':wire.pop('evaluated_at')
    elif mutation=='extra':wire['unexpected']='untrusted-value'
    elif mutation=='date':wire['evaluated_at']='not-a-date'
    elif mutation=='null':wire['evaluated_at']=None
    elif mutation=='reward_state':wire['reward_state']='unknown_state'
    elif mutation=='status_semantics':wire['task_status']='SUBMITTED';wire['reward_state']='pending'
    # The oracle is the fixed immutable pack, not the candidate's model.
    import mayim_agent
    schema=next((Path(mayim_agent.__file__).parent/'contracts/mag-task-packs/payment_surface_discovery.v1/1.1.0').glob('*/status.schema.json'))
    if mutation == 'date':
        from datetime import datetime
        with pytest.raises(ValueError):datetime.fromisoformat(wire['evaluated_at'])
    else:
        assert list(Draft202012Validator(json.loads(schema.read_text()),format_checker=FormatChecker()).iter_errors(wire))
    client,calls=client_for(wire);args=arguments(base)
    if method=='wait_for_reward':args['max_attempts']=3
    with pytest.raises(TaskTransportError) as caught:getattr(client,method)(**args)
    assert caught.value.code=='TASK_RESPONSE_INVALID' and caught.value.request_bytes_sent is True
    assert len(calls)==1 and 'untrusted-value' not in str(caught.value)

@pytest.mark.parametrize('field,value',[
    ('task_id','different-task'),('submission_id','different-submission'),('observation_id','different-observation'),
    ('task_definition_digest','0'*64),('task_definition_version','1.0.0'),('manifest_sha256','0'*64),
    ('manifest_url','https://kari.mayim-mayim.com/other'),('network','eip155:1'),('asset','ETH'),
    ('asset_address','0x'+'1'*40),('amount_atomic','99999')])
def test_status_binding_checks_remain(field,value):
    base=capture();wire=copy.deepcopy(base);wire[field]=value
    client,_=client_for(wire)
    with pytest.raises(TaskTransportError) as caught:client.get_reward_status(**arguments(base))
    assert caught.value.code=='TASK_RESPONSE_INVALID'

def test_expected_claim_reward_is_still_checked():
    base=capture();args=arguments(base);reward=args['reward'].model_dump();reward['amount_atomic']='10001'
    args['reward']=AgentTaskRewardTerms(**reward)
    client,_=client_for(base)
    with pytest.raises(TaskTransportError) as caught:client.get_reward_status(**args)
    assert caught.value.code=='TASK_RESPONSE_INVALID'

def test_legacy_discovery_keeps_original_extra_ignore_contract():
    base=capture('old-status-approved');wire=copy.deepcopy(base);wire['extra']='ignored';wire['evaluated_at']=42
    client,_=client_for(wire);result=client.get_reward_status(**arguments(base))
    assert type(result) is AgentTaskRewardStatus
    assert 'extra' not in result.model_dump() and 'evaluated_at' not in result.model_dump()

def test_guided_status_snapshot_and_json_roundtrip_preserve_datetime():
    wire=capture();status=AgentTaskRewardStatus.model_validate(wire)
    result=TaskDomainObservationGuidedResult(register_receipt=capture('new-observation-response'),matched_status=status)
    assert result.matched_status.model_dump(mode='json')==wire
    assert result.model_dump(mode='json')['matched_status']==wire
    assert TaskDomainObservationGuidedResult.model_validate_json(result.model_dump_json()).matched_status.model_dump(mode='json')==wire

def test_ambiguous_refresh_preserves_new_definition_and_datetime():
    # Synthetic state transition of the real producer response; no payment claimed.
    prior=capture();prior.update(task_status='REWARD_AMBIGUOUS',reward_state='ambiguous',failure_code='settlement_ambiguous')
    current=capture();current.update(task_status='REWARDED',reward_state='paid',rewarded_at=current['evaluated_at'],reward_tx_hash='0x'+'1'*64)
    old=AgentTaskRewardStatus.model_validate(prior);new=AgentTaskRewardStatus.model_validate(current)
    assert verify_reward_status_transition(old,new).model_dump(mode='json')==current
    wrong=copy.deepcopy(current);wrong['observation_id']='other-observation'
    with pytest.raises(ValueError):verify_reward_status_transition(old,AgentTaskRewardStatus.model_validate(wrong))

def test_scheduled_11_status_not_selected_as_discovery():
    # Both families have a 1.1.0 definition: dispatch must also use schema.
    from mayim_agent.task_models import _discovery_model
    wire=capture();wire['schema_version']='ln_church.agent_task_reward_status.v2_1'
    assert _discovery_model(AgentTaskRewardStatus,wire) is AgentTaskRewardStatus
    client,_=client_for(wire)
    with pytest.raises(TaskTransportError):client.get_reward_status(**arguments(capture()))

@pytest.mark.skipif(not __import__('os').environ.get('MAG_BACKEND_C4_ROOT'),reason='Exact isolated Backend c4 required')
def test_real_producer_completion_loss_and_checkpoint_resume(monkeypatch):
    import time
    from test_mag_sdk_c3_connection import Bridge
    from mayim_agent.task_transport import TaskAmbiguousOutcomeError
    bridge=Bridge()
    try:
        created=bridge.send(dict(control='seed-registration',profile='v17'))
        assert created['status']==201
        transport=TaskTransport(_resolver=lambda *args:['93.184.216.34'],_exchange=bridge.task)
        client=AgentTaskClient(definition_version='1.1.0',_transport=transport)
        info=bridge.send({'control':'info'})
        claim=client.claim_task(created['body']['task_id'],agent_id='synthetic-sdk-c4',reward_address=info['payer'])
        now=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
        observation=dict(schema_version='ln_church.task_domain_observation_submission.v1',submission_id='sub_'+'6'*32,
            observed_domain='example.com',observed_urls=[dict(url='http://example.com:8080/pay',method='GET',status_code=402,media_family='json',observed_at=now)],
            discovered_surfaces=[dict(url='http://example.com:8080/pay',method='GET',status_code=402,surface_type='unknown',observed_at=now)],errors=[],
            safety_profile='public_safe_light',no_payment_to_target=True,not_a_security_scan=True,
            verification_cost_vector=dict(http_requests=1,tool_calls=1,payment_attempts=0,personal_data_required=False,human_confirmation_required=False,
                irreversible_action_attempted=False,login_attempted=False,form_submission_attempted=False,vulnerability_scan_attempted=False))
        original=transport.request
        def lose_completion(method,path,**kwargs):
            response=original(method,path,**kwargs)
            if path.endswith('/completion'):
                raise TaskAmbiguousOutcomeError('COMPLETION_OUTCOME_UNKNOWN',request_bytes_sent=True)
            return response
        monkeypatch.setattr(transport,'request',lose_completion)
        checkpoints=[]
        result=client.submit_and_complete_domain_observation(claim.credential,observation,checkpoint_sink=checkpoints.append)
        assert result.completion_receipt is None and result.matched_status.task_status=='SUBMITTED'
        assert result.model_dump(mode='json')['matched_status']['evaluated_at'] is None
        count=len(bridge.calls)
        again=client.submit_and_complete_domain_observation(claim.credential,observation,checkpoint=checkpoints[-1])
        assert again.matched_status.model_dump()==result.matched_status.model_dump()
        assert all(not p.endswith('/domain-observations') for _,p in bridge.calls[count:])
        assert bridge.send({'control':'info'})['counts']['settle']==1
    finally:bridge.close()
