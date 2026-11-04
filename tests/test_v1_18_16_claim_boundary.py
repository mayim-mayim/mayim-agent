"""P section 3 SDK surface: real legacy pack pins, isolated HTTP response double.

No keys, signatures, external requests or producer policy simulation. These
tests do not certify the P Backend guard or the unreceived c2 contract packs.
"""
import copy
import hashlib
import json

import pytest

from mayim_agent.immediate_visit_client import AgentImmediateVisitClient
from mayim_agent.immediate_visit_transport import (
    ImmediateVisitTransport, ImmediateVisitRawResponse,
    ImmediateVisitAPIError, ImmediateVisitTransportError,
)
from mayim_agent.paid_service_trial_client import PaidServiceTrialTaskClient
from mayim_agent.paid_service_trial_transport import (
    PaidServiceTrialTransport, PaidServiceTrialRawResponse,
    PaidServiceTrialAPIError, PaidServiceTrialTransportError,
)
from mayim_agent.paid_service_trial_journal import PaidServiceTrialJournal, _ClaimRequest
from mayim_agent import paid_service_trial_contract as paid

TASK = 'task_' + '1' * 32
ADDRESS = '0x' + '12' * 20
CASES = [(family, version) for family in ('immediate', 'paid') for version in ('v1', 'v2', 'v3')]


def claim_payload(family, version):
    # Test-only response data; all contract identities come from unchanged packs.
    if family == 'immediate':
        url = 'https://example.com/'
        return dict(
            schema_version='ln_church.agent_task_claim_response.immediate_visit.' + version,
            task_id=TASK, task_type='immediate_http_visit.' + version,
            execution_id='exec_' + '2' * 32, claim_token='A' * 43,
            claimed_at='2026-09-21T00:00:00.000Z', report_deadline='2026-09-21T00:10:00.000Z',
            reward_address=ADDRESS, reward_address_control_verified=False,
            profile_id='immediate_visit_utf8.' + version,
            endpoints=[dict(endpoint_id='ep_' + hashlib.sha256(url.encode()).hexdigest(), url=url)],
            repeat_policy='allow', reward=dict(network='eip155:8453', asset='USDC',
                asset_address='0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
                base_amount_atomic='7500', bonus_amount_atomic='7500', maximum_amount_atomic='15000'))
    purchase_terms = dict(x402_version=2, authorization_method='EIP-3009', requirements=dict(
        scheme='exact', network=paid.NETWORK, asset=paid.ASSET, amount='1000',
        payTo='0x' + '34' * 20, maxTimeoutSeconds=900, extra={'name': 'USD Coin', 'version': '2'}))
    reward = dict(network=paid.NETWORK, asset='USDC', asset_address=paid.ASSET, amount_atomic='20000')
    public = dict(purchase_terms=purchase_terms, plan_id='C40', registration_amount_atomic='1000000',
                  capacity_total=40, repeat_policy='ALLOW_REPEAT', reward=reward)
    if version == 'v1':
        public['endpoint'] = 'https://seller.example.com/paid'
    else:
        request = paid.prepare_request(dict(method='GET', url='https://seller.example.com/paid', body=None))
        public.update(request=request, request_digest=paid.v2_digest(request),
                      purchase_terms_digest=paid.purchase_terms_digest(request, purchase_terms, version))
    claim = {key: value for key, value in public.items()
             if key not in ('plan_id', 'registration_amount_atomic', 'capacity_total')}
    claim.update(schema_version='ln_church.agent_task_claim_response.paid_service_trial.' + version,
        task_id=TASK, task_type='paid_service_trial.' + version, task_definition_version=version[1:] + '.0.0',
        task_definition_digest=paid.load_contract_bundle(version)['manifest']['task_definition_digest'],
        terms_digest=paid.digest(public) if version == 'v1' else paid.v2_digest(public),
        execution_id='execution~original', claim_token='A' * 43, reward_address=ADDRESS,
        reward_address_control_verified=False, claim_accepted_at='2026-09-21T00:00:00.000Z',
        report_deadline='2026-09-21T00:10:00.000Z', purchase_block_timestamp_exclusive_min='1789948800')
    return claim


class Exchange:
    def __init__(self, family, version):
        self.family, self.version = family, version
        self.original = claim_payload(family, version)
        self.calls = []
        self.recovery_headers = []
        self.saved_request = None
        self.mode = 'success'

    def __call__(self, method, path, query, headers, body, timeout_seconds):
        request = (method, path, query, headers['Idempotency-Key'], body)
        self.calls.append(request)
        self.recovery_headers.append(headers.get('X-LN-Claim-Recovery'))
        response_type = ImmediateVisitRawResponse if self.family == 'immediate' else PaidServiceTrialRawResponse
        if self.mode in ('reject', 'undeclared'):
            name = 'immediate_visit' if self.family == 'immediate' else 'paid_service_trial'
            payload = dict(schema_version='ln_church.task_error.' + name + '.' + self.version,
                code='active_claim_exists', message='synthetic private diagnostic', request_id='test-request')
            if self.family == 'paid' and self.version != 'v1':
                payload['reason'] = None
            if self.mode == 'undeclared':
                payload['admission_revision'] = 'must-not-enter-legacy-DTO'
            return response_type(409, {}, json.dumps(payload).encode())
        if self.saved_request is None:
            self.saved_request = request
        assert request == self.saved_request
        if self.mode == 'lost':
            error_type = ImmediateVisitTransportError if self.family == 'immediate' else PaidServiceTrialTransportError
            raise error_type('TIMEOUT', request_bytes_sent=True)
        return response_type(200, {}, json.dumps(copy.deepcopy(self.original)).encode())


def client_for(exchange, directory):
    if exchange.family == 'immediate':
        return AgentImmediateVisitClient(version=exchange.version,
            transport=ImmediateVisitTransport(version=exchange.version, exchange=exchange))
    return PaidServiceTrialTaskClient(version=exchange.version, claim_directory=directory,
        transport=PaidServiceTrialTransport(version=exchange.version, exchange=exchange))


def claim(client, key='original-operation'):
    return client.claim_task(TASK, 'synthetic-agent', ADDRESS, idempotency_key=key)


def recover(client, family):
    if family == 'immediate':
        return client.recover_claim(TASK, 'synthetic-agent', ADDRESS, idempotency_key='original-operation')
    return client.recover_claim(TASK, idempotency_key='original-operation')


def private(claim):
    return claim._private_payload()


@pytest.mark.parametrize('family,version', CASES)
def test_existing_409_is_definite_and_has_no_automatic_retry(tmp_path, family, version):
    exchange = Exchange(family, version)
    exchange.mode = 'reject'
    client = client_for(exchange, tmp_path)
    error_type = ImmediateVisitAPIError if family == 'immediate' else PaidServiceTrialAPIError
    with pytest.raises(error_type) as error:
        claim(client)
    assert error.value.status_code == 409
    assert error.value.public_error_code == 'active_claim_exists'
    assert len(exchange.calls) == 1
    assert 'synthetic private diagnostic' not in str(error.value)
    if family == 'paid':
        assert _ClaimRequest(tmp_path, version, TASK, 'original-operation').read()['state'] == 'REJECTED'
        with pytest.raises(error_type):
            recover(client_for(exchange, tmp_path), family)
        assert len(exchange.calls) == 1


@pytest.mark.parametrize('family,version', CASES)
def test_lost_response_recovers_original_identity_and_saved_reward(tmp_path, family, version):
    exchange = Exchange(family, version)
    exchange.mode = 'lost'
    client = client_for(exchange, tmp_path)
    error_type = ImmediateVisitTransportError if family == 'immediate' else PaidServiceTrialTransportError
    with pytest.raises(error_type) as error:
        claim(client)
    assert error.value.code == 'CLAIM_OUTCOME_UNKNOWN'
    assert len(exchange.calls) == 1
    exchange.mode = 'success'
    recovered = recover(client_for(exchange, tmp_path), family)
    assert recovered.execution_id == exchange.original['execution_id']
    assert recovered.reward.model_dump() == exchange.original['reward']
    assert recovered._claim_token_value() == exchange.original['claim_token']
    assert len(exchange.calls) == 2
    assert exchange.calls[0] == exchange.calls[1]
    assert exchange.recovery_headers == [None, '1']
    if family == 'paid':
        saved = PaidServiceTrialJournal.load_claim(tmp_path, TASK, recovered.execution_id)
        assert private(saved) == private(recovered)
        assert saved.purchase_terms.requirements.amount == '1000'
        assert saved.reward.amount_atomic == '20000'


@pytest.mark.parametrize('family,version', CASES)
def test_rejected_new_operation_does_not_destroy_original_claim(tmp_path, family, version):
    exchange = Exchange(family, version)
    original = claim(client_for(exchange, tmp_path))
    exchange.mode = 'reject'
    error_type = ImmediateVisitAPIError if family == 'immediate' else PaidServiceTrialAPIError
    with pytest.raises(error_type):
        claim(client_for(exchange, tmp_path), key='different-operation')
    exchange.mode = 'success'
    recovered = recover(client_for(exchange, tmp_path), family)
    assert recovered.execution_id == original.execution_id
    assert recovered.reward == original.reward
    assert recovered._claim_token_value() == original._claim_token_value()


@pytest.mark.parametrize('family,version', CASES)
def test_undeclared_admission_field_does_not_relax_legacy_error_schema(tmp_path, family, version):
    exchange = Exchange(family, version)
    exchange.mode = 'undeclared'
    error_type = ImmediateVisitTransportError if family == 'immediate' else PaidServiceTrialTransportError
    with pytest.raises(error_type) as error:
        claim(client_for(exchange, tmp_path))
    assert error.value.code == 'CLAIM_OUTCOME_UNKNOWN'
    assert len(exchange.calls) == 1
