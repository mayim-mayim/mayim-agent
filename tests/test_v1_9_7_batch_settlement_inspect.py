import pytest
import json
import base64
from unittest.mock import patch, MagicMock

from mayim_agent.cli import inspect_url
from mayim_agent.client import Payment402Client
from mayim_agent.models import ParsedChallenge, ChallengeSource, PaymentPolicy
from mayim_agent.exceptions import PaymentExecutionError

def _mock_402_response(accepts_array: list) -> MagicMock:
    mock_res = MagicMock()
    mock_res.status_code = 402
    mock_res.content = b""
    mock_res.url = "http://public.example"
    
    payload = {"accepts": accepts_array}
    b64_str = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')
    mock_res.headers = {"PAYMENT-REQUIRED": b64_str}
    
    return mock_res


@patch("mayim_agent.inspect_transport._exchange_once")
def test_a_batch_settlement_inspect_classification(mock_req):
    """A. batch-settlement accepts[] の inspect 分類が正しいことを確認"""
    mock_req.return_value = _mock_402_response([{
        "scheme": "batch-settlement",
        "network": "eip155:8453",
        "asset": "USDC",
        "amount": "100",
        "payTo": "0xabc",
        "extra": {
            "receiverAuthorizer": "0xdef",
            "withdrawDelay": 900,
            "assetTransferMethod": "transferWithAuthorization"
        }
    }])

    res = inspect_url("http://public.example")

    assert res.ok is True
    assert res.recommended_action == "observe_only"
    assert res.diagnostic_class == "deferred_batch_settlement_observed"
    
    assert "x402" in res.settlement_rails_detected
    assert "batch-settlement" not in res.settlement_rails_detected
    
    assert res.will_execute_payment is False
    
    assert res.selected_settlement_option is not None
    sel = res.selected_settlement_option
    assert sel.scheme == "batch-settlement"
    assert sel.rail == "x402"
    assert sel.execution_support == "observe_only"
    assert sel.settlement_model == "deferred_batch"
    assert sel.authorization_artifact == "voucher"
    assert sel.deferred_settlement is True


@patch("mayim_agent.inspect_transport._exchange_once")
def test_b_not_misclassified_as_exact(mock_req):
    """B. batch-settlement が exact や未知として誤分類されないこと"""
    mock_req.return_value = _mock_402_response([{
        "scheme": "batch-settlement",
        "network": "solana:123"
    }])
    
    res = inspect_url("http://public.example")
    
    assert res.selected_settlement_option.scheme == "batch-settlement"
    assert res.diagnostic_class == "deferred_batch_settlement_observed"
    assert res.diagnostic_class != "post_settlement_proof_required"




def test_d_execution_guard():
    """D. client.py の実行経路で絶対的にブロックされること (Policy状態に依存しない)"""
    
    parsed = ParsedChallenge(
        scheme="batch-settlement",
        network="eip155:137",
        amount=1.0,
        asset="USDC",
        parameters={},
        source=ChallengeSource.STANDARD_X402
    )
    
    # 1. Policy = None の場合でも防ぐこと
    client_no_policy = Payment402Client(policy=None)
    with pytest.raises(PaymentExecutionError, match="batch_settlement_execution_not_supported"):
        client_no_policy._process_payment(parsed, {}, {})

    # 2. ユーザーが強引に Policy.allowed_schemes に "batch-settlement" を入れた場合でも防ぐこと
    policy_overridden = PaymentPolicy(allowed_schemes=["batch-settlement"])
    client_with_policy = Payment402Client(policy=policy_overridden)
    with pytest.raises(PaymentExecutionError, match="batch_settlement_execution_not_supported"):
        client_with_policy._process_payment(parsed, {}, {})
