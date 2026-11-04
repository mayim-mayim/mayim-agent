import pytest
import io
import sys
from unittest.mock import patch, MagicMock
from mayim_agent.client import MagClient, validate_public_domain_for_observation
from mayim_agent.models import DomainObservationSlotResponse, DomainObservationResultSubmission

def test_public_import():
    try:
        from mayim_agent import validate_public_domain_for_observation
        assert callable(validate_public_domain_for_observation)
    except ImportError as e:
        pytest.fail(f"Public import failed: {e}")

def test_validate_public_domain_for_observation():
    assert validate_public_domain_for_observation("example.com") is True
    assert validate_public_domain_for_observation("api.v2.example-domain.org") is True

    assert validate_public_domain_for_observation("localhost") is False
    assert validate_public_domain_for_observation("127.0.0.1") is False
    assert validate_public_domain_for_observation("10.0.0.5") is False
    assert validate_public_domain_for_observation("169.254.169.254") is False
    assert validate_public_domain_for_observation("metadata.google.internal") is False

    assert validate_public_domain_for_observation("https://example.com") is False
    assert validate_public_domain_for_observation("example.com:8080") is False
    assert validate_public_domain_for_observation("example.com/path") is False
