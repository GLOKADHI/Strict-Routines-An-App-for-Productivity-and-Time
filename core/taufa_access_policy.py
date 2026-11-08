"""Readiness is based on documented API configuration (Taufa AI Primary + Official OpenAI API Fallback)."""
import hashlib
import threading
import time

CONVERSATION_ID = '891a3c1e-eed2-460e-bc05-6b8ac94bec5b'
_verified = {}
_lock = threading.Lock()


def configuration_key(endpoint, api_key):
    return hashlib.sha256((endpoint + '\n' + api_key).encode()).hexdigest()


def mark_verified(endpoint, api_key):
    with _lock:
        _verified[configuration_key(endpoint, api_key)] = time.monotonic()


def evaluate_taufa_access_policy(validator=None):
    if validator is None:
        from core.taufa_ai_validator import TaufaAiValidator
        validator = TaufaAiValidator
    configured = validator.is_configured()
    with _lock:
        checked = _verified.get(configuration_key(validator.get_endpoint() or '', validator.get_api_key() or ''))
    verified = bool(configured and checked is not None and 0 <= time.monotonic() - checked <= 3600)
    has_active_session = False
    try:
        from core.taufa_client import TaufaClient
        tc = TaufaClient()
        has_active_session = bool(tc.has_active_session())
    except Exception:
        pass
    taufa_ready = verified or has_active_session
    return {
        "taufa_ready": taufa_ready,
        "configured": configured or has_active_session,
        "verified": verified,
        "has_active_session": has_active_session,
        "message": (
            "Taufa AI designated session is authenticated and ready."
            if has_active_session
            else "Taufa AI designated thread verified and ready."
            if verified
            else "Taufa AI API configured."
            if configured
            else "Taufa AI primary session not configured."
        ),
    }


def status(validator):
    pol = evaluate_taufa_access_policy(validator)
    configured = pol["configured"]
    verified = pol["verified"]
    has_active_session = pol["has_active_session"]

    openai_configured = False
    try:
        from core.openai_fallback_validator import OpenAiFallbackValidator
        openai_configured = bool(OpenAiFallbackValidator.is_configured())
    except Exception:
        pass

    authenticated = verified or has_active_session or openai_configured
    active_provider = (
        "TAUFA_AI"
        if (verified or has_active_session or configured)
        else ("OPENAI_FALLBACK" if openai_configured else "UNAVAILABLE")
    )
    state = (
        'READY'
        if (verified or has_active_session)
        else 'READY_OPENAI_FALLBACK'
        if openai_configured
        else 'CONFIGURED_NOT_VERIFIED'
        if configured
        else 'OFFICIAL_API_NOT_CONFIGURED'
    )
    message = (
        'AI STATUS: CONNECTED – TAUFA AI'
        if (has_active_session or verified)
        else 'AI STATUS: CONNECTED – OPENAI FALLBACK'
        if openai_configured
        else 'Verify the configured API with an isolated Demo sample.'
        if configured
        else 'AI STATUS: UNAVAILABLE'
    )
    return dict(
        success=True,
        configured=configured or has_active_session or openai_configured,
        authenticated=authenticated,
        conversation_id=CONVERSATION_ID,
        official_api_verified=verified,
        has_active_session=has_active_session,
        openai_fallback_configured=openai_configured,
        active_provider=active_provider,
        state=state,
        message=message,
    )
