"""Scheduled multipart adapter for the existing explicit registration client.

Only private, memory-held request/payment capabilities are created. The owner
verifies origin control and freezes terms before the existing payment signature.
"""
import copy
import re
import uuid
from urllib.parse import urlsplit

from .offer_registration import (RegistrationQuote, _Private, OfferRegistrationError,
    PATH, _requirements, c, decode_base64_object, validate_eip3009_payload)
from ._contract_schema import validate_economic_wire
from .task_v2_contract import validate_manifest_bytes, validate_opaque_id


def _headers(control_key, content_type=None):
    if type(control_key) is not str or re.fullmatch(r'ock_v1_[A-Za-z0-9_-]{43}', control_key) is None:
        raise ValueError('Invalid offer control key.')
    h = {'X-LN-Offer-Control-Key': control_key}
    if content_type is not None:
        h['Content-Type'] = content_type
    return h


def _resource():
    return dict(url=c.PUBLIC_API_ORIGIN+PATH, mimeType='application/json',
        description='Register scheduled_http_get_batch.v1 through the shared strict x402 business-commit boundary.')


class ScheduledRegistrationQuote(RegistrationQuote):
    __slots__ = ('_metadata', '_manifest', '_headers', '_preview', '_origin')

    @property
    def saved_conditions(self):
        result = {k: self._metadata[k] for k in ('task_definition_version', 'activity_map_policy_id', 'activity_map_epoch')}
        result.update({
            'registration_terms_sha256': self._preview['registration_terms_sha256'],
            'activity_map_origin': self._origin})
        return result

    def _signed_operation(self, header):
        return ScheduledSignedRegistration(self, header)


class ScheduledSignedRegistration(_Private):
    __slots__ = ('_quote', '_header', '_sent', '_result', '_lock')

    def __init__(self, quote, header):
        import threading
        envelope = decode_base64_object(header)
        if set(envelope) != {'x402Version','accepted','resource','payload'} or envelope['x402Version'] != 2 or envelope['resource'] != _resource():
            raise ValueError('Invalid Scheduled authorization.')
        _requirements(quote._metadata, envelope['accepted'])
        if envelope['accepted'] != quote.payment_terms:
            raise ValueError('Changed Scheduled payment conditions.')
        auth = envelope['payload']['authorization']
        validate_eip3009_payload(envelope['payload'], expected_signer=auth['from'], chain_id=8453,
            token_address=c.ASSET, asset='USDC', atomic_amount=envelope['accepted']['amount'],
            pay_to=envelope['accepted']['payTo'], now=int(auth['validAfter'])+1, max_valid_before=int(auth['validBefore']))
        self._quote, self._header = quote, header
        self._sent, self._result, self._lock = False, None, threading.Lock()

    @property
    def operation_ref(self):
        return self._quote._metadata['registration_intent_id']


def prepare(client, metadata, manifest, control_key, timeout):
    validate_economic_wire('scheduled_mag', 'registration', metadata)
    validate_economic_wire('scheduled_mag', 'manifest', manifest)
    from .task_economic_terms import validate_economic_terms
    validate_economic_terms('scheduled_mag', metadata['economic_terms'])
    if metadata['plan_id'] != metadata['economic_terms']['plan_id']:
        raise ValueError('Changed Scheduled plan.')
    manifest_bytes = c.canonical_bytes(manifest, 32768)
    import hashlib
    validate_manifest_bytes(manifest_bytes, hashlib.sha256(manifest_bytes).hexdigest())
    parts = urlsplit(manifest['urls'][0])
    origin = parts.scheme + '://' + parts.netloc
    boundary = 'mayim-scheduled-' + uuid.uuid4().hex
    body = b''
    for name, data in [('metadata', c.canonical_bytes(metadata, 65536)), ('manifest', manifest_bytes)]:
        body += ('--'+boundary+'\r\nContent-Disposition: form-data; name="'+name+'"\r\n'
            'Content-Type: application/json; charset=utf-8\r\n\r\n').encode() + data + b'\r\n'
    body += ('--'+boundary+'--\r\n').encode()
    headers = _headers(control_key, 'multipart/form-data; boundary='+boundary)
    status, response_headers, value = client._send(PATH, body, timeout, private_headers=headers)
    if status != 402:
        raise OfferRegistrationError('REGISTRATION_REJECTED', status_code=status)
    challenge = decode_base64_object(response_headers['payment-required'])
    if set(challenge) != {'x402Version','resource','accepts'} or type(challenge['x402Version']) is not int or challenge['x402Version'] != 2 or challenge['resource'] != _resource() or len(challenge['accepts']) != 1:
        raise ValueError('Invalid Scheduled payment preview.')
    _requirements(metadata, challenge['accepts'][0])
    # The Gateway publishes the existing owner preview in the 402 body.
    validate_economic_wire('scheduled_mag', 'registration-preview', value)
    if value['registration_intent_id'] != metadata['registration_intent_id']:
        raise ValueError('Changed Scheduled registration identity.')
    quote = ScheduledRegistrationQuote(body, challenge)
    quote._metadata, quote._manifest, quote._headers = copy.deepcopy(metadata), manifest_bytes, headers
    quote._preview, quote._origin = copy.deepcopy(value), origin
    return quote


def submit(client, operation, replay, timeout):
    if not isinstance(operation, ScheduledSignedRegistration) or type(replay) is not bool:
        raise ValueError('Invalid Scheduled operation.')
    with operation._lock:
        if operation._result is not None:
            return copy.deepcopy(operation._result)
        if operation._sent and not replay:
            raise OfferRegistrationError('REGISTRATION_OUTCOME_UNKNOWN')
        operation._sent = True
        try:
            q = operation._quote
            status, _, value = client._send(PATH, q._body, timeout, operation._header, private_headers=q._headers)
            if status in (200, 201):
                validate_economic_wire('scheduled_mag', 'registration-result', value)
                if any(value[k] != q._metadata[k] for k in ('registration_intent_id','task_type','scheduled_at','plan_id')):
                    raise ValueError('Changed Scheduled result identity.')
                import hashlib
                if value['manifest_sha256'] != hashlib.sha256(q._manifest).hexdigest():
                    raise ValueError('Changed Scheduled Manifest.')
                operation._result = copy.deepcopy(value)
                return value
        except Exception:
            pass
        raise OfferRegistrationError('REGISTRATION_OUTCOME_UNKNOWN')


def read(client, ref, control_key, timeout):
    ref = validate_opaque_id(ref, 'registration_intent_id')
    status, _, value = client._send('/api/agent/task-offer-registrations/'+ref, b'', timeout,
        method='GET', private_headers=_headers(control_key))
    if status != 200:
        raise OfferRegistrationError('REGISTRATION_REJECTED', status_code=status)
    validate_economic_wire('scheduled_mag', 'registration-recovery-status', value)
    if value['registration_intent_id'] != ref:
        raise ValueError('Changed Scheduled recovery identity.')
    return copy.deepcopy(value)
