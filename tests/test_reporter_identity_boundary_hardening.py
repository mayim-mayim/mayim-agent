import pytest
import time
from unittest.mock import patch, MagicMock
from mayim_agent.client import MagClient

def test_ensure_reporter_verification_accepts_safety_flags():
    """Test that ensure_reporter_verification parses new safety flags without breaking."""
    client = MagClient(private_key="0x" + "1"*64)
    
    # 動的な現在時刻（ミリ秒）と未来の有効期限を生成
    now_ms = int(time.time() * 1000)
    future_ms = now_ms + 600000 # 10分後
    
    with patch.object(client, "execute_request") as mock_exec:
        # 1. Challenge fetch
        mock_exec.side_effect = [
            {
                "challenge_id": "chal_123", 
                "message": "LN Church Reporter Verification\npurpose=reporter_key_control\n...", 
                "expires_at": "2030-01-01T00:00:00Z"
            },
            # 2. Verify response with new v1.12.1 flags
            {
                "schema_version": "agent_identity_verify_response.v1",
                "status": "verified",
                "reporter_verification_status": "key_control_verified",
                "reporter_verification_method": "nonce_signature",
                "reporter_public_key_type": "evm",
                "verified_at": now_ms,
                "verified_until": future_ms, # <- ここを未来の時刻に修正
                "proof_id": "proof_opaque_audit_handle_123",
                "verification_semantics": "key_control_only",
                "not_a_trust_score": True,
                "not_report_truth": True,
                "not_payment_proof": True,
                "not_a_recommendation": True,
                "not_a_verdict": True
            }
        ]
        
        res = client.ensure_reporter_verification()
        
        assert res["status"] == "verified"
        assert res["not_a_trust_score"] is True
        assert res["not_report_truth"] is True
        assert client._reporter_proof_id == "proof_opaque_audit_handle_123"
        assert mock_exec.call_count == 2
        
        # Test Cache hit still works (有効期限内なのでAPIは呼ばれない)
        res_cached = client.ensure_reporter_verification()
        assert res_cached["status"] == "cached"
        assert mock_exec.call_count == 2

def test_force_refresh_bypasses_cache():
    """Test that force_refresh=True skips cache and calls API again."""
    client = MagClient(private_key="0x" + "1"*64)
    
    # Inject fake cache
    client._reporter_verified_until = 9999999999999
    client._reporter_proof_id = "proof_old"
    
    # Normally this hits cache
    res_cached = client.ensure_reporter_verification()
    assert res_cached["status"] == "cached"
    
    with patch.object(client, "execute_request") as mock_exec:
        mock_exec.side_effect = [
            {"challenge_id": "chal_999", "message": "msg", "expires_at": ""},
            {"status": "verified", "verified_until": 9999999999999, "proof_id": "proof_new"}
        ]
        
        # Force refresh
        res_force = client.ensure_reporter_verification(force_refresh=True)
        assert res_force["status"] == "verified"
        assert client._reporter_proof_id == "proof_new"
        assert mock_exec.call_count == 2
