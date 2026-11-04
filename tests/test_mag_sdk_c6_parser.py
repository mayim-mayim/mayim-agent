"""Captured body, deployed header aliases, canonical binding and policy boundary.

The body fixture is the unchanged independent capture. Header values below are
synthetic instances of the final handler shape; live final-artifact coverage is
in test_mag_sdk_c6_handlers.py. No relayer or real provider is contacted.
"""
import base64
import copy
from decimal import Decimal
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from mayim_agent.challenges import parse_challenge_from_response, TRUSTED_TOKENS
from mayim_agent.client import MagClient
from mayim_agent.crypto.evm import TRUSTED_EIP3009_TOKENS
from mayim_agent.exceptions import PaymentChallengeError, PaymentExecutionError
from mayim_agent.models import PaymentPolicy

CAPTURE = Path(__file__).parent / 'fixtures/mag/c6/real-402.json'
URL = 'https://kari.mayim-mayim.com/api/agent/activity-map/v1/purchases'
OTHER = '0x' + '2' * 40


def fixture():
    return json.loads(CAPTURE.read_bytes())['payment_challenge']


def encode(value):
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')


def aliases(body):
    c = body['challenge']; p = c['parameters']
    header = dict(network=c['network'], amount=str(c['amount']), asset=c['asset'],
        destination=p['payTo'], token_address=p['asset'], decimals=p['decimals'],
        challenge='synthetic-opaque-alias-token',
        accepts=[{k:p[k] for k in ('scheme','network','asset','symbol','decimals','amount','payTo')}],
        resource=c['resource'], scheme='lnc-evm-relay',
        parameters=dict(relayer_endpoint=p['relayer_endpoint']))
    return {
        'PAYMENT-REQUIRED': encode(header),
        'x-402-payment-required': f"price={c['amount']}; asset={c['asset']}; network={c['network']}",
        'WWW-Authenticate': f'x402 token="{header["challenge"]}", network="{c["network"]}"',
    }


def response(body, headers=None):
    r = requests.Response(); r.status_code = 402; r.url = URL
    r._content = json.dumps(body).encode(); r.headers.update(headers or {})
    return r


def client_for(r, policy=None):
    c = MagClient(private_key='11'*32, auto_navigate=False, policy=policy)
    c._request_sync = Mock(return_value=r)
    c.evm_signer.generate_eip3009_payload_atomic = Mock(side_effect=AssertionError('Signer must not run'))
    return c


def execute(c):
    return c.execute_detailed('POST', URL, payload={'operation_id':'a'*32},
                             headers={'Idempotency-Key':'a'*32})


@pytest.mark.parametrize('with_headers', [False, True])
def test_captured_display_atomic_pair_has_exact_requirement(with_headers):
    body = fixture(); before = copy.deepcopy(body)
    p = parse_challenge_from_response(response(body, aliases(body) if with_headers else None))
    req = p._canonical_requirement
    assert body == before
    assert p.scheme == req.scheme == 'exact'
    assert req.source_origin == 'body_display_atomic'
    assert req.network == 'eip155:137' and req.chain_id == 137
    assert req.atomic_amount == '10000' and req.human_amount_decimal == Decimal('0.01')
    assert req.decimals == 6 and req.asset == 'USDC'
    assert p.parameters['_raw_outer_amount'] == 0.01
    assert p.parameters['_raw_resource'] == {'url':URL, 'method':'POST'}
    trusted = TRUSTED_EIP3009_TOKENS[(137, req.token_address_or_mint)]
    assert TRUSTED_TOKENS[f'eip155:137_{req.token_address_or_mint}'] == dict(asset=trusted.asset,decimals=trusted.decimals)
    c = MagClient(auto_navigate=False)
    c._bind_known_legacy_challenge_to_request(p,request_url=URL,method='POST',idempotency_key='a'*32)
    assert p._canonical_requirement['resource_url'] == URL
    assert p._canonical_requirement['method'] == 'POST'
    assert p._canonical_requirement['idempotency_key'] == 'a'*32
    c._validate_exact_canonical_alignment(p,p._signer_requirement,p.parameters['_raw_accepted'])
    assert c._estimate_usd_decimal(p) == Decimal('0.01')


@pytest.mark.parametrize('path,value', [
    (('scheme',),'unknown'), (('amount',),0.010000000000000002), (('amount',),10000),
    (('asset',),'JPYC'), (('network',),'eip155:8453'), (('decimals',),18),
    (('chainId',),8453), (('payTo',),OTHER), (('token_address',),OTHER),
    (('parameters','scheme'),'other'), (('parameters','network'),'eip155:999'),
    (('parameters','network'),None), (('parameters','asset'),OTHER),
    (('parameters','symbol'),'JPYC'), (('parameters','decimals'),18),
    (('parameters','decimals'),True), (('parameters','amount'),'10001'),
    (('parameters','amount'),'10000.0'), (('parameters','amount'),10000),
    (('parameters','payTo'),OTHER), (('parameters','destination'),OTHER),
    (('parameters','token_address'),OTHER), (('parameters','chainId'),8453),
    (('resource','url'),'https://other.invalid/purchases'), (('resource','method'),'GET'),
])
def test_real_mismatch_rejected_before_signer(path, value):
    body = fixture(); target = body['challenge']
    for key in path[:-1]: target = target[key]
    target[path[-1]] = value
    c = client_for(response(body))
    with pytest.raises((PaymentChallengeError, PaymentExecutionError)):
        execute(c)
    c.evm_signer.generate_eip3009_payload_atomic.assert_not_called()
    assert c._request_sync.call_count == 1


@pytest.mark.parametrize('field,value', [
    ('network','eip155:8453'), ('amount','0.02'), ('asset','JPYC'),
    ('destination',OTHER), ('token_address',OTHER), ('decimals',18),
    ('resource',{'url':URL,'method':'GET'}), ('scheme','unknown'),
    ('challenge','different-token'), ('parameters',{'relayer_endpoint':'https://other.invalid'}),
    ('accepts',[]),
])
def test_header_body_conflict_rejected_before_signer(field, value):
    body=fixture(); h=aliases(body)
    payload=json.loads(base64.urlsafe_b64decode(h['PAYMENT-REQUIRED']+'==='))
    payload[field]=value; h['PAYMENT-REQUIRED']=encode(payload)
    c=client_for(response(body,h))
    with pytest.raises(PaymentChallengeError): execute(c)
    c.evm_signer.generate_eip3009_payload_atomic.assert_not_called()


@pytest.mark.parametrize('name,value', [
    ('x-402-payment-required','price=0.02; asset=USDC; network=eip155:137'),
    ('x-402-payment-required','price=0.01; asset=JPYC; network=eip155:137'),
    ('x-402-payment-required','price=0.01; asset=USDC; network=eip155:8453'),
    ('WWW-Authenticate','x402 token="different", network="eip155:137"'),
    ('WWW-Authenticate','x402 token="synthetic-opaque-alias-token", network="eip155:8453"'),
    ('PAYMENT-REQUIRED',None),
])
def test_every_deployed_alias_is_checked(name,value):
    body=fixture(); h=aliases(body)
    if value is None: del h[name]
    else: h[name]=value
    with pytest.raises(PaymentChallengeError): parse_challenge_from_response(response(body,h))


@pytest.mark.parametrize('policy', [
    PaymentPolicy(max_spend_per_tx_usd=0.009),
    PaymentPolicy(max_spend_per_session_usd=0.009),
    PaymentPolicy(allowed_networks=['eip155:8453']),
])
def test_deployed_body_keeps_policy_and_budget_before_signer(policy):
    body=fixture(); c=client_for(response(body,aliases(body)),policy)
    with pytest.raises(PaymentExecutionError): execute(c)
    c.evm_signer.generate_eip3009_payload_atomic.assert_not_called()
    assert c._request_sync.call_count == 1


def test_retained_display_mutation_fails_canonical_recheck():
    p=parse_challenge_from_response(response(fixture()))
    c=MagClient(auto_navigate=False)
    p.parameters['_raw_outer_amount']=0.02
    with pytest.raises(PaymentExecutionError,match='outer.amount'):
        c._validate_exact_canonical_alignment(p,p._canonical_requirement,p.parameters['_raw_accepted'])


@pytest.mark.parametrize('field,value', [('_raw_outer_amount',0.02),('payTo',OTHER)])
def test_post_approval_mutation_cannot_reach_signer(field,value):
    body=fixture();c=client_for(response(body,aliases(body)))
    original=c._process_payment
    def mutate(parsed,headers,payload,**kwargs):
        assert parsed._approved_signer_snapshot and parsed._approved_canonical_snapshot
        parsed.parameters[field]=value
        return original(parsed,headers,payload,**kwargs)
    c._process_payment=mutate
    with pytest.raises(PaymentExecutionError): execute(c)
    c.evm_signer.generate_eip3009_payload_atomic.assert_not_called()
    assert c._request_sync.call_count==1
