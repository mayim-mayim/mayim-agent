import json
import ipaddress
import re
from typing import Dict, Any, Optional

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    raise ImportError("Install with `pip install mayim-agent[mcp]`")

from ..cli import (
    inspect_url,
    _public_asset,
    _public_intent,
    _public_network,
    _public_scheme,
    _public_selection_reason,
    _public_x402_pay_to,
)
from ..grant_signals import STRONG_TERMS, WEAK_TERMS
from ..inspect_transport import (
    InspectTransportError,
    _canonicalize_target,
    _require_global_address,
)
from ..redaction import (
    QUERY_REDACTION,
    _contains_inspect_secret_material,
    redact_inspect_public_url,
)


_SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)(?:authorization|proxy-authorization|cookie|set-cookie)\s*[:=]"),
    re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(?:lnbc|lntb|lnbcrt)[0-9][a-z0-9]{20,}"),
    re.compile(r"(?i)\b(?:macaroon|preimage|private[_ -]?key|payment[_ -]?signature|receipt[_ -]?token)\s*[:=]"),
    re.compile(r"(?i)\b(?:secret|credential|private_key|payment_signature|receipt_token|access_token|refresh_token)\b"),
    re.compile(r"(?i)(?:^|[^a-z0-9])(?:secret|credential|private[_ -]?key|payment[_ -]?signature|receipt[_ -]?token|access[_ -]?token|refresh[_ -]?token)(?:$|[^a-z0-9])"),
    re.compile(r"^(?:0x)?[0-9a-fA-F]{64}$"),
    re.compile(r"^[1-9A-HJ-NP-Za-km-z]{45,128}$"),
)

_PUBLIC_HANDOFF_MODES = frozenset({"guided_handoff"})
_PUBLIC_OPERATOR_REASONS = frozenset({
    "malformed_or_unsupported_settlement_hint",
    "allowed_network_mismatch",
    "commerce_surface_with_settlement_rail",
    "commerce_surface_detected",
})
_PUBLIC_HANDOFF_ITEMS = frozenset({
    "quote_details", "mandate_scope", "expiration", "revocation_method",
    "settlement_rail_options", "receipt_or_proof_model", "cart_details",
    "price_breakdown", "merchant_identity", "checkout_expiration",
    "payment_token_scope", "order_receipt_model", "broker_identity",
    "escrow_terms", "settlement_method", "dispute_policy",
    "treat_mandate_as_settlement_proof",
    "execute_payment_without_operator_approval", "store_raw_mandate_payload",
    "treat_shared_payment_token_as_settlement_proof",
    "execute_checkout_without_operator_approval",
    "store_raw_shared_payment_token", "treat_broker_hint_as_settlement_proof",
    "enter_escrow_without_operator_approval",
    "store_raw_broker_or_session_token", "explicit_price", "settlement_rail",
    "receipt_model", "cart_total", "order_receipt", "quote",
    "escrow_or_dispute_terms", "settlement_rail_not_declared",
    "network_not_declared", "asset_not_declared",
    "post_payment_artifact_unknown",
})
_PUBLIC_GRANT_TERMS = frozenset(STRONG_TERMS | WEAK_TERMS)
_PUBLIC_GRANT_SIGNAL_TYPES = frozenset({
    "faucet", "trial_credit", "developer_credit", "promotional_credit",
    "coupon_or_discount", "loyalty_reward", "access_entitlement",
    "sponsored_grant", "unknown_grant_like",
})
_PUBLIC_GRANT_SOURCE_KINDS = frozenset({"body_json", "body_text", "headers"})
_GRANT_SIGNAL_REASON = (
    "Grant-like signals are observed only. Redeemability and availability "
    "are not verified."
)

# ==========================================
# 🔍 Inspect-Only MCP Server Initialization
# ==========================================
mcp = FastMCP("mAG_Inspect_Node")

def _contains_secret_keys(obj: Any) -> bool:
    """
    Recursively check if a dictionary contains keys that match raw secret names.
    """
    rejected_keys = {
        "authorization", "www-authenticate", "payment-signature", "payment-response",
        "macaroon", "preimage", "private_key", "grant_token", "mandate_token",
        "shared_payment_token", "access_token", "refresh_token", "secret", "api_key"
    }

    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in rejected_keys:
                return True
            if _contains_secret_keys(v):
                return True
    elif isinstance(obj, list):
        for item in obj:
            if _contains_secret_keys(item):
                return True
    return False


def _contains_secret_material(obj: Any) -> bool:
    if type(obj) is dict:
        return any(_contains_secret_material(value) for value in obj.values())
    if type(obj) is list:
        return any(_contains_secret_material(value) for value in obj)
    if isinstance(obj, str):
        return (
            _contains_inspect_secret_material(obj)
            or any(
                pattern.search(obj) is not None
                for pattern in _SECRET_VALUE_PATTERNS
            )
        )
    return False


def _public_settlement_pay_to(rail: Any, value: Any) -> Any:
    if str(rail).upper() in {"L402", "MPP"}:
        return QUERY_REDACTION
    return value


def _public_grant_signal_payload(value: Any) -> Dict[str, Any]:
    """Serialize the sidecar without attacker-controlled field/key names."""

    def public_list(name: str, allowed: frozenset) -> list:
        candidate = getattr(value, name, [])
        if type(candidate) is not list:
            return []
        return sorted({
            item for item in candidate[:64]
            if type(item) is str and item in allowed
        })

    def public_bool(name: str, default: bool = False) -> bool:
        candidate = getattr(value, name, default)
        return candidate if type(candidate) is bool else default

    confidence = getattr(value, "confidence", "none")
    if confidence not in {"none", "low", "medium", "high"}:
        confidence = "none"
    transferability = getattr(value, "transferability_declared", None)
    if type(transferability) is not bool:
        transferability = None
    requires_identity = getattr(value, "requires_identity", None)
    if type(requires_identity) is not bool:
        requires_identity = None

    return {
        "detected": public_bool("detected"),
        "confidence": confidence,
        "signal_types": public_list(
            "signal_types", _PUBLIC_GRANT_SIGNAL_TYPES
        ),
        "source_kinds": public_list(
            "source_kinds", _PUBLIC_GRANT_SOURCE_KINDS
        ),
        "detected_terms": public_list("detected_terms", _PUBLIC_GRANT_TERMS),
        "detected_fields": public_list("detected_fields", _PUBLIC_GRANT_TERMS),
        "machine_readable": public_bool("machine_readable"),
        "redeemability_verified": public_bool("redeemability_verified"),
        "availability_verified": public_bool("availability_verified"),
        "redemption_endpoint_present": public_bool(
            "redemption_endpoint_present"
        ),
        "verification_endpoint_present": public_bool(
            "verification_endpoint_present"
        ),
        "eligibility_declared": public_bool("eligibility_declared"),
        "scope_declared": public_bool("scope_declared"),
        "expiration_declared": public_bool("expiration_declared"),
        "transferability_declared": transferability,
        "requires_identity": requires_identity,
        "recommended_action": "observe_only",
        "diagnostic_class": "grant_like_signal_observed",
        "not_a_recommendation": True,
        "not_a_verdict": True,
        "unassessed_is_not_failed": True,
        "reason": _GRANT_SIGNAL_REASON,
    }

@mcp.tool()
def inspect_paid_surface(url: str, method: str = "GET") -> Dict[str, Any]:
    """
    Safely inspect an unknown URL for HTTP 402 / commerce surfaces (AP2/ACP/OKX APP/L402/x402/MPP).
    Does NOT execute any payments. Does NOT require a private key or wallet.
    """
    result = inspect_url(url, method=method)

    # 💡 v1.9.5: Serialize Settlement Options safely for MCP output
    settlement_opts = []
    for opt in getattr(result, "settlement_options", []):
        settlement_opts.append({
            "rail": opt.rail,
            "scheme": opt.scheme,
            "network": opt.network,
            "chain_family": opt.chain_family,
            "chain_name_hint": opt.chain_name_hint,
            "asset": opt.asset,
            "amount": opt.amount,
            "pay_to": (
                _public_settlement_pay_to(opt.rail, opt.pay_to)
                if str(opt.rail).upper() in {"L402", "MPP"}
                else _public_x402_pay_to(opt.pay_to, opt.network)
            ),
            "source": opt.source,
            "execution_support": opt.execution_support,
            "selected": opt.selected,
            "selection_reason": opt.selection_reason,
            "settlement_model": getattr(opt, "settlement_model", None),
            "authorization_artifact": getattr(opt, "authorization_artifact", None),
            "finality_model": getattr(opt, "finality_model", None),
            "requires_channel_state": getattr(opt, "requires_channel_state", None),
            "deferred_settlement": getattr(opt, "deferred_settlement", None)
        })

    selected_opt = None
    if getattr(result, "selected_settlement_option", None):
        opt = result.selected_settlement_option
        selected_opt = {
            "rail": opt.rail,
            "scheme": opt.scheme,
            "network": opt.network,
            "chain_family": opt.chain_family,
            "asset": opt.asset,
            "amount": opt.amount,
            "execution_support": opt.execution_support,
            "selected": opt.selected,
            "selection_reason": opt.selection_reason,
            "settlement_model": getattr(opt, "settlement_model", None),
            "authorization_artifact": getattr(opt, "authorization_artifact", None),
            "finality_model": getattr(opt, "finality_model", None),
            "requires_channel_state": getattr(opt, "requires_channel_state", None),
            "deferred_settlement": getattr(opt, "deferred_settlement", None)
        }

    observatory_metadata = None
    if getattr(result, "ln_church_observatory", None):
        observatory_metadata = result.ln_church_observatory.model_dump()

    public_method = method.upper() if type(method) is str else "INVALID"
    if public_method not in {"GET", "HEAD"}:
        public_method = "INVALID"

    return {
        "schema_version": "ln_church_agent.mcp.inspect_result.v1",
        "ok": result.ok,
        "url": redact_inspect_public_url(result.url),
        "method": public_method,
        "status_code": result.http_status,
        "error_stage": result.error_stage,
        "failure_class": result.failure_class,
        "failure_reason": result.failure_reason,
        "diagnostic_class": result.diagnostic_class,
        "recommended_action": result.recommended_action,
        "surfaces_detected": result.surfaces_detected,
        "settlement_rails_detected": result.settlement_rails_detected,
        "rails_detected": result.rails_detected,
        "surface_type": result.surface_type or "unknown",
        "commerce_intent": result.commerce_intent or "unknown",
        "authorization_artifact": result.authorization_artifact or "none",
        "detection_confidence": result.detection_confidence or "unknown",
        "detection_reason": result.detection_reason or "none",
        "unsupported_reason": result.unsupported_reason,
        "will_execute_payment": False,
        # --- v1.9.1 Guided Handoff fields ---
        "handoff_mode": getattr(result, "handoff_mode", None),
        "approval_required": getattr(result, "approval_required", None),
        "operator_approval_reason": getattr(result, "operator_approval_reason", None),
        "ask_site_for": getattr(result, "ask_site_for", []),
        "do_not": getattr(result, "do_not", []),
        "required_evidence": getattr(result, "required_evidence", []),
        "missing_information": getattr(result, "missing_information", []),
        # --- v1.11.2: Grant-like Signal Sidecar) ---
        "grant_signal_detected": getattr(result, "grant_signal_detected", False),
        "grant_signals": (
            _public_grant_signal_payload(result.grant_signals)
            if getattr(result, "grant_signals", None) else None
        ),
        # --- v1.9.5 Settlement Options & Observatory Metadata ---
        "settlement_options": settlement_opts,
        "selected_settlement_option": selected_opt,
        "ln_church_observatory": observatory_metadata,
        "safety": {
            "inspect_only": True,
            "payment_performed": False,
            "requires_private_key": False,
            "secrets_redacted": True
        }
    }

@mcp.tool()
def explain_recommended_action(inspect_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Explains the recommended_action from an inspect result, guiding the AI on what to do next.
    """
    source = inspect_result if type(inspect_result) is dict else {}
    action = source.get("recommended_action", "unknown")
    if type(action) is not str or action not in {
        "pay_and_verify",
        "observe_only",
        "stop_safely",
        "reject_invalid",
        "no_payment_required",
    }:
        action = "unknown"
    meaning = "Unknown action."
    safe_next_step = "Do nothing."

    if action == "pay_and_verify":
        meaning = "The endpoint is a standard settlement rail (e.g., L402, x402). Payment is requested."
        safe_next_step = "Use a fully configured payment execution engine if approved. Note: THIS inspect-only MCP server does NOT execute payments."
    elif action == "observe_only":
        meaning = "The endpoint is an observable commerce surface (e.g., AP2, ACP, or x402 exact). Do not execute autonomously."
        safe_next_step = "Wait for human operator review or log the surface."
    elif action == "stop_safely":
        meaning = "The endpoint requires a high-intent flow (e.g., session) or contains a malformed/unsupported payload."
        safe_next_step = "Halt execution safely and escalate to operator."
    elif action == "reject_invalid":
        meaning = "The 402 challenge shape is completely invalid or unparseable."
        safe_next_step = "Ignore this endpoint."
    elif action == "no_payment_required":
        meaning = "No HTTP 402 or commerce surface was detected."
        safe_next_step = "Proceed as a normal web request."

    handoff_mode = _public_enum(
        source.get("handoff_mode"),
        _PUBLIC_HANDOFF_MODES,
    )
    if handoff_mode == "guided_handoff":
        safe_next_step = (
            "Do not execute payment in this MCP server. Review ask_site_for, do_not, "
            "required_evidence, and missing_information, then route to operator approval "
            "or a separate managed payment execution engine if approved."
        )

    return {
        "schema_version": "ln_church_agent.mcp.action_explanation.v1",
        "recommended_action": action,
        "meaning": meaning,
        "safe_next_step": safe_next_step,
        "payment_execution_available_in_this_mcp": False,
        "handoff_mode": handoff_mode,
        "approval_required": source.get("approval_required")
        if type(source.get("approval_required")) is bool else None,
        "operator_approval_reason": _public_enum(
            source.get("operator_approval_reason"),
            _PUBLIC_OPERATOR_REASONS,
        ),
        "ask_site_for": _public_handoff_list(source.get("ask_site_for", [])),
        "do_not": _public_handoff_list(source.get("do_not", [])),
        "required_evidence": _public_handoff_list(
            source.get("required_evidence", [])
        ),
        "missing_information": _public_handoff_list(
            source.get("missing_information", [])
        ),
    }

def _public_enum(
    value: Any,
    allowed: frozenset,
    default: Optional[str] = None,
) -> Optional[str]:
    if type(value) is str and value in allowed:
        return value
    return default


def _public_handoff_list(value: Any) -> list:
    if not isinstance(value, (list, tuple)):
        return []
    return [
        item for item in value[:32]
        if type(item) is str and item in _PUBLIC_HANDOFF_ITEMS
    ]


def main():
    mcp.run()

if __name__ == "__main__":
    main()
