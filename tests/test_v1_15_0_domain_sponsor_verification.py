import pytest
import os
import io
import sys
import json
from unittest.mock import patch, MagicMock

from mayim_agent.client import MagClient
from mayim_agent.models import (
    DomainSponsorVerification,
    DomainSponsorChallengeResponse,
    DomainSponsorVerifyResponse,
    DomainSponsorVerificationSummary,
    DomainObservationRequestStatus,
    DomainObservationDomainReadModel
)

# ==========================================
# 1. Model Tests
# ==========================================
def test_models_default_safety_flags():
    """安全線（not_a_verdict等）がデフォルトで保持されていること"""
    ch = DomainSponsorChallengeResponse(request_id="obsreq_1", domain="test.com", challenge_id="c1", challenge_url="url")
    assert ch.not_a_verdict is True
    assert ch.not_a_security_scan is True

    vr = DomainSponsorVerifyResponse(request_id="obsreq_1", domain="test.com")
    assert vr.not_legal_ownership_proof is True
    assert vr.verification_scope == "domain_control_not_legal_ownership"

    su = DomainSponsorVerificationSummary()
    assert su.not_legal_ownership_proof is True
    assert su.not_a_recommendation is True

def test_observation_models_parse_sponsor_fields():
    """既存の Read Model モデルが新規オブジェクトを適切にパースできること"""
    req_status = DomainObservationRequestStatus(
        request_id="obsreq_1",
        domain="test.com",
        status="active",
        sponsor_verification={"sponsor_verification_status": "verified"}
    )
    assert req_status.sponsor_verification is not None
    assert req_status.sponsor_verification.sponsor_verification_status == "verified"

    domain_rm = DomainObservationDomainReadModel(
        domain="test.com",
        sponsor_verification_summary={"has_verified_domain_sponsor": True}
    )
    assert domain_rm.sponsor_verification_summary is not None
    assert domain_rm.sponsor_verification_summary.has_verified_domain_sponsor is True

# ==========================================
# 2. Client Tests
# ==========================================




# ==========================================
# 3. Save Helper Tests
# ==========================================

# ==========================================
# 4. CLI Tests (Safety and Formats)
# ==========================================
