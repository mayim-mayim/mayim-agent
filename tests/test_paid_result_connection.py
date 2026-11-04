"""Common X1/X2 and SDK-AC8 at actual sync/async HTTP entry points."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
import requests

from mayim_agent.client import MagClient, Payment402Client
from mayim_agent.exceptions import PaymentExecutionError
from mayim_agent.models import ExecutionContext


HANDLE = "pr_" + "a" * 32
REQUEST_HASH = "sha256:" + "b" * 64
EXPIRY = "2026-10-09T00:00:00Z"
METADATA = {"result_handle": HANDLE, "request_hash": REQUEST_HASH, "result_expires_at": EXPIRY}
HEADERS = {"X-LN-Result-Handle": HANDLE, "X-LN-Request-Hash": REQUEST_HASH, "X-LN-Result-Expires-At": EXPIRY}
PURCHASES = [
    ("/api/agent/monzen/goal-attempts/surface-comparison-facts", 200),
    ("/api/bazaar/surface_comparison_facts", 200),
    ("/api/bazaar/domain-observation-slots", 201),
    ("/api/bazaar/verified-domain-tracks", 200),
]


def response(status, body, headers=None):
    res = requests.Response()
    res.status_code = status
    res._content = json.dumps(body).encode()
    res.headers.update(headers or {})
    return res


def transport(monkeypatch, client, async_mode, replies):
    send = AsyncMock(side_effect=replies) if async_mode else MagicMock(side_effect=replies)
    if async_mode:
        client._async_client = MagicMock()
        client._async_client.request = send
    else:
        monkeypatch.setattr("mayim_agent.client.requests.request", send)
    return send


def execute(client, async_mode, method, path, **kwargs):
    if async_mode:
        return asyncio.run(client.execute_detailed_async(method, path, **kwargs))
    return client.execute_detailed(method, path, **kwargs)


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("path,status", PURCHASES)
def test_header_only_metadata_reaches_purchaser_copy_before_redaction(monkeypatch, async_mode, path, status):
    client = Payment402Client(base_url="https://buyer.test")
    body = {"value": "original purchase", "expires_at": "product expiry",
            "result_retrieval": {"by_handle": "/api/bazaar/paid-results/" + HANDLE}}
    send = transport(monkeypatch, client, async_mode, [response(status, body, {
        **HEADERS, "Location": "/api/bazaar/paid-results/" + HANDLE,
        "Link": f'</api/bazaar/paid-results?request_hash={REQUEST_HASH}>; rel="result"',
    })])
    result = execute(client, async_mode, "POST", path)
    assert result.response == body and send.call_count == 1
    assert result.paid_result_metadata == METADATA
    result.paid_result_metadata.clear()
    assert result.paid_result_metadata == METADATA
    for view in (repr(result), result.model_dump_json(), str(result.response_headers)):
        assert HANDLE not in view and REQUEST_HASH not in view


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("suffix", ["/" + HANDLE, "?tx_hash=0x" + "c" * 64 + "&request_hash=" + REQUEST_HASH])
@pytest.mark.parametrize("status,code", [(200, None), (400, None), (404, None), (410, None), (402, None),
                                       (500, "PAID_RESULT_PERSISTENCE_UNCONFIRMED"), (502, "BACKEND_RESPONSE_PARSE_FAILED")])
def test_recovery_is_nonpurchase_and_preserves_real_status(monkeypatch, async_mode, suffix, status, code):
    client = Payment402Client(base_url="https://buyer.test", allow_unsafe_navigate=True)
    client._restore_session_spend_from_evidence = MagicMock(side_effect=AssertionError("budget restore"))
    client._restore_session_spend_from_evidence_async = AsyncMock(side_effect=AssertionError("budget restore"))
    client._process_payment = MagicMock(side_effect=AssertionError("payment"))
    body = {"value": "original", "result_handle": HANDLE, "request_hash": REQUEST_HASH} if status == 200 else {
        "message": f"Use retrieval endpoint with handle {HANDLE} and {REQUEST_HASH}", "error_code": code,
        "next_action": {"url": "/buy-again", "method": "POST"},
    }
    send = transport(monkeypatch, client, async_mode, [response(status, body, {**HEADERS, "Location": "/buy-again"})])
    path = "/api/bazaar/paid-results" + suffix
    if status == 200:
        result = execute(client, async_mode, "GET", path)
        assert result.response == body
        assert result.paid_result_metadata == METADATA
        for view in (repr(result), result.model_dump_json()):
            assert HANDLE not in view and REQUEST_HASH not in view
    else:
        with pytest.raises(PaymentExecutionError) as caught:
            execute(client, async_mode, "GET", path)
        assert caught.value.status_code == status and caught.value.code == code
        assert str(status) in str(caught.value)
        assert HANDLE not in str(caught.value) and REQUEST_HASH not in str(caught.value)
    assert send.call_count == 1 and send.call_args.args[0] == "GET"


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("code", ["STATE_UPDATE_FAILED", "PAID_RESULT_PERSISTENCE_UNCONFIRMED"])
def test_unconfirmed_response_does_not_follow_purchase_navigation(monkeypatch, async_mode, code):
    client = Payment402Client(base_url="https://buyer.test", allow_unsafe_navigate=True)
    body = {"status": "error", "error_code": code, "message": "Reconciliation required", "retryable": False,
            "next_action": {"url": "/buy-again", "method": "POST"}}
    send = transport(monkeypatch, client, async_mode, [response(500, body, {"Link": '</buy-again>; rel="next"'})])
    with pytest.raises(PaymentExecutionError) as caught:
        execute(client, async_mode, "POST", PURCHASES[0][0], payload={"paymentOverride": {"type": "grant", "proof": "fixture"}})
    assert caught.value.status_code == 500 and caught.value.code == code
    assert send.call_count == 1 and client.policy._session_spent_usd == 0


@pytest.mark.parametrize("async_mode", [False, True])
def test_paid_fact_survives_partial_result_save_failure(monkeypatch, async_mode):
    from test_v1_16_2_payment_boundary import _l402_402, TEST_PREIMAGE
    wallet = MagicMock()
    wallet.pay_invoice.return_value = TEST_PREIMAGE
    client = Payment402Client(ln_adapter=wallet)
    context = ExecutionContext()
    failure = response(500, {"error_code": "PAID_RESULT_PERSISTENCE_UNCONFIRMED", "message": "Saved result unconfirmed"})
    send = transport(monkeypatch, client, async_mode, [_l402_402(), failure])
    with pytest.raises(PaymentExecutionError) as caught:
        execute(client, async_mode, "GET", "https://buyer.test/start", context=context)
    assert caught.value.status_code == 500 and caught.value.code == "PAID_RESULT_PERSISTENCE_UNCONFIRMED"
    assert wallet.pay_invoice.call_count == 1 and send.call_count == 2
    assert client.policy._session_spent_usd == pytest.approx(0.00065)
    assert client.last_receipt.payment_performed is True




@pytest.mark.parametrize("headers,expected", [({}, {}), ({"X-LN-Result-Handle": HANDLE}, {"result_handle": HANDLE}),
                                           ({"x-ln-church-result-handle": "old-alias"}, {"result_handle": "old-alias"}),
                                           ({"X-LN-Result-Handle": "", "x-ln-church-result-handle": "old-alias"}, {"result_handle": "old-alias"})])
def test_partial_metadata_and_track_alias_do_not_invent_hash_or_expiry(monkeypatch, headers, expected):
    client = Payment402Client(base_url="https://buyer.test")
    transport(monkeypatch, client, False, [response(200, {"expires_at": "product"}, headers)])
    result = client.execute_detailed("POST", "/api/bazaar/verified-domain-tracks")
    assert result.paid_result_metadata == expected
    assert "old-alias" not in str(result.response_headers)


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("content", [b"", b"not JSON"])
def test_recovery_does_not_invent_success_for_missing_or_invalid_json(monkeypatch, async_mode, content):
    client = Payment402Client(base_url="https://buyer.test")
    res = response(200, {})
    res._content = content
    send = transport(monkeypatch, client, async_mode, [res])
    with pytest.raises(PaymentExecutionError) as caught:
        execute(client, async_mode, "GET", "/api/bazaar/paid-results/" + HANDLE)
    assert caught.value.status_code == 200 and caught.value.code is None
    assert send.call_count == 1




@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("verification", [
    "verified", "unsigned", "signature_only", "wrong_payment_id",
    "wrong_requirement_hash", "unsettled", "invalid_signature",
])
def test_exact_partial_save_retains_only_independently_bound_paid_fact(monkeypatch, async_mode, verification):
    from test_p0_2_receipts import _b64url, _jws
    from test_v1_16_2_payment_boundary import EVM_PRIVATE_KEY, _exact_402, _CaptureEvidence
    evidence = _CaptureEvidence()
    client = Payment402Client(private_key=EVM_PRIVATE_KEY, evidence_repo=evidence)
    context = ExecutionContext()
    claims = {}
    signature = MagicMock(side_effect=lambda token: None if verification == "invalid_signature" else dict(claims))
    settlement = MagicMock(side_effect=lambda value: verification != "unsettled")
    client._receipt_signature_verifier = signature
    client._receipt_settlement_binding_checker = None if verification == "signature_only" else settlement
    http_calls = []

    def reply(method, url, **kwargs):
        http_calls.append((method, url, kwargs))
        assert method == "POST" and url == "https://buyer.test/start"
        if len(http_calls) == 1:
            return _exact_402()
        assert len(http_calls) == 2
        assert "payment-signature" in {name.lower() for name in kwargs["headers"]}
        requirement = client._last_parsed_challenge._canonical_requirement
        claims.update({name: requirement[name] for name in ("payment_id", "requirement_hash")})
        if verification == "wrong_payment_id":
            claims["payment_id"] = "other-payment"
        if verification == "wrong_requirement_hash":
            claims["requirement_hash"] = "sha256:" + "e" * 64
        token = _b64url(claims) if verification == "unsigned" else _jws(claims)
        return response(500, {"error_code": "PAID_RESULT_PERSISTENCE_UNCONFIRMED", "message": "Result save unconfirmed"},
                        {"PAYMENT-RESPONSE": token})

    transport(monkeypatch, client, async_mode, reply)
    with pytest.raises(PaymentExecutionError) as caught:
        execute(client, async_mode, "POST", "https://buyer.test/start", context=context)
    assert (caught.value.status_code, caught.value.code) == (500, "PAID_RESULT_PERSISTENCE_UNCONFIRMED")
    assert len(http_calls) == 2
    assert signature.call_count == (0 if verification == "unsigned" else 1)
    assert settlement.call_count == (1 if verification in {"verified", "unsettled"} else 0)
    verified = verification == "verified"
    assert client.last_receipt.settlement_verified is verified
    assert client.last_receipt.payment_performed is verified
    assert client.last_receipt.delivered is False
    assert client.policy._session_spent_usd == (1 if verified else 0)
    assert client.policy._session_reserved_usd == (0 if verified else 1)
    operation_id = client.last_receipt.payment_id

    def recover(outcome):
        if async_mode:
            return asyncio.run(client.resolve_ambiguous_payment_async(context, operation_id, outcome))
        return client.resolve_ambiguous_payment(context, operation_id, outcome)

    if verified:
        before = client.get_payment_operation_states(context)
        with pytest.raises(PaymentExecutionError, match="Known-settled"):
            recover("confirmed_not_paid")
        assert client.get_payment_operation_states(context) == before
        assert recover("confirmed_paid") == "completed"
        assert recover("confirmed_paid") == "completed"
        with pytest.raises(PaymentExecutionError):
            recover("confirmed_not_paid")
        assert client.policy._session_spent_usd == 1
        assert sum(record.session_spend_delta_usd or 0 for record in evidence.records) == 1
        assert sum(record.session_budget_event == "confirmed" for record in evidence.records) == 1
    else:
        assert recover("confirmed_not_paid") == "confirmed_not_paid"
        assert client.policy._session_spent_usd == 0
        assert all(not record.payment_performed for record in evidence.records)
    assert client.policy._session_reserved_usd == 0
    assert len(http_calls) == 2


@pytest.mark.parametrize('async_mode', [False, True])
def test_purchaser_body_stays_private_across_outcome_evidence(monkeypatch, async_mode):
    from mayim_agent.models import EvidenceRepository, OutcomeSummary
    exports = []
    body = {'access_path': 'sponsored_grant', 'result_handle': HANDLE, 'request_hash': REQUEST_HASH, 'result_retrieval': {'by_handle': '/api/bazaar/paid-results/' + HANDLE}}

    class Repo(EvidenceRepository):

        def export_evidence(self, record, context):
            exports.append((record.model_dump_json(), context.model_dump_json()))
    context = ExecutionContext()

    def outcome(data, receipt, ctx):
        assert data == body
        ctx.hints.update(data)
        return OutcomeSummary(is_success=True, message=HANDLE, external_evidence=data)
    client = MagClient(agent_id='private-copy-test', base_url='https://buyer.test', evidence_repo=Repo())
    transport(monkeypatch, client, async_mode, [response(200, body, HEADERS)])
    result = execute(client, async_mode, 'POST', PURCHASES[0][0], context=context, outcome_matcher=outcome)
    assert result.response == body and result.outcome.external_evidence == body
    assert context.hints['result_handle'] == HANDLE
    assert len(exports) == 1
    for view in (repr(result), result.model_dump_json(), *exports[0]):
        assert HANDLE not in view and REQUEST_HASH not in view
