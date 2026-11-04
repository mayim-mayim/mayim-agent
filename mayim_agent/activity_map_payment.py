"""Graph-only wire adapter over the existing policy-governed payment runtime."""
import base64
import copy
import json
from types import MethodType

import requests

from .activity_map import ActivityMapError
from .activity_map_contract import PREFIX, ORIGIN


def _proof(headers, payload):
    result = {}
    for key, value in headers.items():
        lower = key.lower()
        if lower == 'authorization' and not value.startswith('MAG-Recovery '):
            result['X-MAG-Payment-Authorization'] = value
        elif lower in ('payment-signature', 'x-payment', 'x-payment-signature'):
            result[key] = value
    for key, name in (('paymentAuth','X-MAG-Payment-Auth'), ('paymentOverride','X-MAG-Payment-Override')):
        if key in payload:
            if key == 'paymentOverride' and payload[key].get('type') == 'grant':
                raise ActivityMapError('GRANT_NOT_SUPPORTED')
            if key == 'paymentOverride' and payload[key].get('type') == 'faucet':
                raise ActivityMapError('FAUCET_NOT_SUPPORTED')
            result[name] = base64.urlsafe_b64encode(json.dumps(payload[key],separators=(',',':')).encode()).decode().rstrip('=')
    return result


def pay_once(client, d, initial_headers, initial_raw):
    if d['payment_started'] or d['payment_headers']:
        raise ActivityMapError('PAYMENT_ALREADY_ATTEMPTED')
    # Use an isolated adapter instance; do not alter the caller's methods,
    # navigation settings, signer configuration or shared payment policy.
    runtime = copy.copy(client.payment_runtime)
    runtime.auto_navigate = False
    runtime.max_hops = 0
    runtime.max_payment_retries = 1
    first = True
    original_process = type(runtime)._process_payment

    def process(self, parsed, headers, payload, **kwargs):
        if d['payment_started']:
            raise ActivityMapError('PAYMENT_ALREADY_ATTEMPTED')
        d['payment_started'] = True
        # Persist the fully approved rail/network/asset/request binding before
        # invoking a signer/provider. Unknown outcome keeps this marker.
        snapshot = self._capture_approved_receipt_snapshot(parsed)
        d['payment_identity'] = {'approved_requirement': json.loads(snapshot['canonical_snapshot_json']),
                                 'operation_id':d['operation_id'], 'request_sha256':d['request_sha256'],
                                 'manifest_sha256':d['manifest_sha256'], 'reference':None}
        client._save(d)
        reference, network, report = original_process(self, parsed, headers, payload, **kwargs)
        d['payment_headers'] = _proof(headers,payload)
        d['payment_identity'].update(reference=reference,network=network,
            payment_hash=getattr(report,'payment_hash',None),settlement_confirmed=False)
        client._save(d)  # Before proof-bearing HTTP or any retry.
        return reference, network, report

    def request(self, method, url, kwargs, context):
        nonlocal first
        if method != 'POST' or url != ORIGIN + PREFIX + '/purchases':
            raise ActivityMapError('PAYMENT_ROUTE_MISMATCH')
        if first:
            first = False
            status, headers = 402, initial_headers
            raw = json.dumps(json.loads(initial_raw)['payment_challenge']).encode()
        else:
            if not d['payment_started'] or not d['payment_headers']:
                raise ActivityMapError('PAYMENT_PROOF_UNAVAILABLE')
            # Only the initial purchase route may dispatch an approved payment.
            # Send the unchanged original request with the already-saved proof;
            # the Backend's operation fence makes transport retries idempotent.
            # Later process recovery uses /recover and cannot initiate payment.
            status,headers,raw=client._request('POST',PREFIX+'/purchases',d=d,
                body=d['request'])
            if status in (200,202):
                client._adopt(d,raw)
            # Recovery never creates or processes another challenge.
            if status == 402:
                raise ActivityMapError('PAYMENT_UNCONFIRMED',402)
        response=requests.Response();response.status_code=status
        response.headers.update(headers);response._content=raw;response.url=url
        return response

    runtime._process_payment=MethodType(process,runtime)
    runtime._request_sync=MethodType(request,runtime)
    runtime.execute_detailed('POST',PREFIX+'/purchases',payload=copy.deepcopy(d['request']),
        headers={'Authorization':'MAG-Recovery '+d['recovery_secret'],'Idempotency-Key':d['operation_id']})
