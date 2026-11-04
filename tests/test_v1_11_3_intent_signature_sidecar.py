import pytest
import asyncio
from unittest.mock import patch, MagicMock
from mayim_agent.client import MagClient

def _get_client():
    return MagClient(private_key="0x" + "1" * 64)





@patch("requests.request")
def test_f_no_automatic_telemetry_hook(mock_req):
    """Test F: No automatic telemetry hook in standard execution paths"""
    client = MagClient(base_url="https://api.test", private_key="0x" + "1"*64)
    mock_res = MagicMock(status_code=200, content=b'{"status":"success"}')
    mock_res.json.return_value = {"status": "success"}
    mock_req.return_value = mock_res

    client.execute_request("POST", "/api/v1/resource", payload={"test": "data"})
    assert mock_req.call_count == 1
    assert "observe" not in mock_req.call_args.args[1]


def test_h_no_payment_behavior_changed():
    """Test H: Structural confirmation that core models haven't mutated execution rules"""
    from mayim_agent.capabilities import get_capability_matrix
    matrix = get_capability_matrix()
    l402 = next((r for r in matrix if r["id"] == "l402"), None)
    assert l402["execution_behavior"] == "execute"
    assert l402["proof_semantics"] == "verified"

def test_i_intent_signature_is_secret_stripped_but_not_entity_validated():
    """Test I: Explicitly document that shape-only is currently caller responsibility."""
    client = _get_client()
    clean = client._strip_secrets_from_evidence({
        "intent_signature": {
            "target_object": "OpenAI",
            "input_shape": ["株式会社Xの非公開調査をしてください"],
            "api_key": "SECRET"
        }
    })

    # Entity values are NOT validated/stripped by the current SDK layer
    assert clean["intent_signature"]["target_object"] == "OpenAI"
    assert clean["intent_signature"]["input_shape"] == ["株式会社Xの非公開調査をしてください"]
    # But secrets ARE strictly stripped
    assert "api_key" not in clean["intent_signature"]
