import pytest
import json
import base64
from unittest.mock import patch, MagicMock

# Import directly to ensure it loads without any AGENT_PRIVATE_KEY setup
from mayim_agent.integrations.mcp_inspect import inspect_paid_surface, explain_recommended_action, _contains_secret_keys

@patch("mayim_agent.inspect_transport._exchange_once")
def test_mcp_inspect_paid_surface_never_executes_payment(mock_req):
    """Ensure the inspect tool returns will_execute_payment=False and requires no keys."""
    mock_res = MagicMock(status_code=402, url="http://public.example")
    mock_res.headers = {"WWW-Authenticate": 'L402 macaroon="mac", invoice="inv"'}
    mock_res.content = b""
    mock_res.json.side_effect = ValueError()
    mock_req.return_value = mock_res

    res = inspect_paid_surface("http://public.example")
    
    assert res["status_code"] == 402
    assert "L402" in res["settlement_rails_detected"]
    assert res["recommended_action"] == "pay_and_verify"
    assert res["will_execute_payment"] is False
    assert res["safety"]["inspect_only"] is True
    assert res["safety"]["payment_performed"] is False
    assert res["safety"]["requires_private_key"] is False

@patch("mayim_agent.inspect_transport._exchange_once")
def test_mcp_ap2_acp_remain_observe_only(mock_req):
    """AP2/ACP surfaces should be classified as observe_only without hitting execution paths."""
    mock_res = MagicMock(status_code=402, url="http://public.example")
    payload = {"protocol": "ap2", "intent": "payment_mandate"}
    mock_res.headers = {"Content-Type": "application/json"}
    mock_res.content = json.dumps(payload).encode()
    mock_res.json.return_value = payload
    mock_req.return_value = mock_res

    res = inspect_paid_surface("http://public.example")
    
    assert "AP2" in res["surfaces_detected"]
    assert "AP2" not in res["settlement_rails_detected"]
    assert res["recommended_action"] == "observe_only"
    assert res["will_execute_payment"] is False

def test_mcp_explain_action():
    """Ensure the explanation clearly states payment execution is disabled."""
    fake_res = {"recommended_action": "pay_and_verify"}
    explanation = explain_recommended_action(fake_res)
    
    assert explanation["recommended_action"] == "pay_and_verify"
    assert "THIS inspect-only MCP server does NOT execute payments" in explanation["safe_next_step"]
    assert explanation["payment_execution_available_in_this_mcp"] is False


# --- 4-1. AP2 guided handoff fields are exposed through MCP ---
@patch("mayim_agent.inspect_transport._exchange_once")
def test_mcp_exposes_guided_handoff_fields(mock_req):
    mock_res = MagicMock(status_code=402, url="http://public.example")
    payload = {"protocol": "ap2", "intent": "payment_mandate"}
    mock_res.headers = {"Content-Type": "application/json"}
    mock_res.content = json.dumps(payload).encode()
    mock_res.json.return_value = payload
    mock_req.return_value = mock_res

    res = inspect_paid_surface("http://public.example")

    assert res["recommended_action"] == "observe_only"
    assert res["handoff_mode"] == "guided_handoff"
    assert res["approval_required"] is True
    assert res["operator_approval_reason"] is not None
    assert "mandate_scope" in res["ask_site_for"]
    assert "treat_mandate_as_settlement_proof" in res["do_not"]
    assert "explicit_price" in res["required_evidence"]
    assert res["will_execute_payment"] is False
    assert res["safety"]["payment_performed"] is False

# --- 4-2. Normal L402 has no guided handoff but remains non-executing in MCP ---
@patch("mayim_agent.inspect_transport._exchange_once")
def test_mcp_l402_has_no_guided_handoff_but_never_executes(mock_req):
    mock_res = MagicMock(status_code=402, url="http://public.example")
    mock_res.headers = {"WWW-Authenticate": 'L402 macaroon="mac", invoice="inv"'}
    mock_res.content = b""
    mock_res.json.side_effect = ValueError()
    mock_req.return_value = mock_res

    res = inspect_paid_surface("http://public.example")

    assert "L402" in res["settlement_rails_detected"]
    assert res["recommended_action"] == "pay_and_verify"
    assert res["handoff_mode"] is None
    assert res["approval_required"] is None
    assert res["ask_site_for"] == []
    assert res["do_not"] == []
    assert res["required_evidence"] == []
    assert res["missing_information"] == []
    assert res["will_execute_payment"] is False

# --- 4-3. observation payload includes handoff summary and remains non-payment ---

# --- 4-4. explanation includes guided handoff context ---
def test_mcp_explain_guided_handoff_context():
    fake_res = {
        "recommended_action": "observe_only",
        "handoff_mode": "guided_handoff",
        "approval_required": True,
        "operator_approval_reason": "commerce_surface_detected",
        "ask_site_for": ["mandate_scope"],
        "do_not": ["treat_mandate_as_settlement_proof"],
        "required_evidence": ["explicit_price"],
        "missing_information": ["merchant_identity"],
    }

    explanation = explain_recommended_action(fake_res)

    assert explanation["payment_execution_available_in_this_mcp"] is False
    assert explanation["handoff_mode"] == "guided_handoff"
    assert explanation["approval_required"] is True
    assert "operator approval" in explanation["safe_next_step"].lower()

# ==========================================
# 🛡️ New Secret Redaction Tests 
# ==========================================

def test_contains_secret_keys_recursive():
    """再帰的なキー名チェックが正しく機能するか単体テスト"""
    
    # OKケース
    safe_obj = {
        "protocol": {
            "authorization_scheme": "L402",
            "payment_intent": "charge"
        },
        "evidence": {
            "payment_performed": False
        },
        "source_channel": "mcp",
        "schema_version": "v1"
    }
    assert _contains_secret_keys(safe_obj) is False

    # NGケース (ネストされたリストの中の辞書にキーがある)
    unsafe_obj = {
        "metadata": [
            {"safe_key": 123},
            {"macaroon": "secret_string"} # ここで検知されるべき
        ]
    }
    assert _contains_secret_keys(unsafe_obj) is True

@patch("mayim_agent.client.Payment402Client.execute_detailed")
@patch("mayim_agent.inspect_transport._exchange_once")
def test_mcp_inspect_never_calls_execute_detailed(mock_req, mock_execute_detailed):
    """
    Ensure the inspect_paid_surface tool purely relies on request/parsing
    and NEVER accidentally triggers the full runtime Payment402Client.execute_detailed loop.
    """
    mock_res = MagicMock(status_code=402, url="http://public.example")
    mock_res.headers = {"WWW-Authenticate": 'L402 macaroon="mac", invoice="inv"'}
    mock_res.content = b""
    mock_res.json.side_effect = ValueError()
    mock_req.return_value = mock_res

    res = inspect_paid_surface("http://public.example")
    
    # 決済実行メソッドが一度も呼ばれていないことを担保する
    mock_execute_detailed.assert_not_called()
    assert res["will_execute_payment"] is False
    assert res["safety"]["requires_private_key"] is False
