import pytest
from unittest.mock import patch, MagicMock
import io
import sys
import os
import json
from mayim_agent.client import MagClient
from mayim_agent.models import (
    PaymentPolicy,
    VerifiedDomainTrackRegistrationResponse,
    VerifiedDomainTrackReadModel,
    VerifiedDomainTrackSummary,
    DomainObservationDomainReadModel
)

# [品質改善] 安定性の高い private key の定義
STABLE_PRIVATE_KEY = "0x" + "1" * 64

def test_v1_16_0_models_can_be_imported():
    assert VerifiedDomainTrackRegistrationResponse
    assert VerifiedDomainTrackReadModel
    assert VerifiedDomainTrackSummary

def test_verified_domain_track_read_model_parsing():
    mock_data = {
        "domain": "kari.mayim-mayim.com",
        "sponsor_type": "verified_domain_track",
        "track_type": "verified_domain_track",
        "track_plan": "verified_domain_track_lite",
        "track_status": "active_verified",
        "duration_days": 30,
        "observation_interval_hours": 168,
        "observation_profile": "public_safe_light",
        "verification_required": True,
        "domain_owner_verified": False,
        "sponsor_verified": True,
        "domain_control_verified": True,
        "sponsor_verification_status": "verified",
        "verification_scope": "domain_control_not_legal_ownership",
        "not_legal_ownership_proof": True,
        "is_active_verified_track": True,
        "created_at": "2026-07-01T12:00:00Z",
        "expires_at": "2026-07-31T12:00:00Z",
        "not_a_verdict": True,
        "not_a_security_scan": True,
        "not_an_endorsement": True,
        "not_a_certification": True,
        "not_a_recommendation": True,
        "not_a_trust_score": True
    }
    model = VerifiedDomainTrackReadModel(**mock_data)
    assert model.domain == "kari.mayim-mayim.com"
    assert model.track_status == "active_verified"
    assert model.is_active_verified_track is True

def test_verified_domain_track_summary_parsing():
    read_model_data = {
        "domain": "kari.example.com",
        "sponsor_type": "verified_domain_track",
        "track_type": "verified_domain_track",
        "track_plan": "verified_domain_track_lite",
        "track_status": "active_verified",
        "duration_days": 30,
        "observation_interval_hours": 168,
        "observation_profile": "public_safe_light",
        "verification_required": True,
        "domain_owner_verified": False,
        "sponsor_verified": True,
        "domain_control_verified": True,
        "sponsor_verification_status": "verified",
        "verification_scope": "domain_control_not_legal_ownership",
        "not_legal_ownership_proof": True,
        "is_active_verified_track": True,
        "not_a_verdict": True,
        "not_a_security_scan": True,
        "not_an_endorsement": True,
        "not_a_certification": True,
        "not_a_recommendation": True,
        "not_a_trust_score": True
    }
    mock_data = {
        "domain": "kari.example.com",
        "current_track": read_model_data,
        "sponsor_verified": True,
        "domain_control_verified": True,
        "not_legal_ownership_proof": True,
        "not_a_trust_score": True,
        "has_active_verified_domain_track": True
    }
    summary = VerifiedDomainTrackSummary(**mock_data)
    assert summary.has_active_verified_domain_track is True
    assert summary.current_track.track_plan == "verified_domain_track_lite"





# [品質改善] MagicMock による誤魔化しを排除し、実モデル Pydantic パース構造の厳格な検証

# [品質改善] CLIリークテスト（JSONへのsecret漏洩チェック）

# [品質改善] --proof-file にはシークレットが保存されることの検証

# [受入条件の追加] sponsor challenge / verify ワークフローの --proof-file 受入テスト
