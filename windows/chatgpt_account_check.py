"""Secret-free result projection for a normal ChatGPT website session check."""
import json

SESSION_URL = 'https://chatgpt.com/api/auth/session'


def account_result(http_status, content_type, payload, expected_email):
    """Never return session tokens, IDs, other account fields or raw responses."""
    base = dict(authenticated=False, account_matches=False, expected_email=expected_email)
    if http_status != 200 or 'json' not in content_type.lower():
        return dict(base, success=False, http_status=http_status,
                    error='ChatGPT did not provide an authenticated session response. Interactive sign-in may be required.')
    try:
        data = json.loads(payload)
        email = data.get('user', {}).get('email') if isinstance(data, dict) else None
    except (ValueError, TypeError, AttributeError):
        email = None
    if not isinstance(email, str) or not email.strip():
        return dict(base, success=False, http_status=http_status,
                    error='The embedded ChatGPT session is not signed in.')
    matches = email.strip().casefold() == expected_email.strip().casefold()
    # Do not print a different account's address. Only echo the requested one
    # after an actual authenticated response establishes a match.
    return dict(base, success=matches, authenticated=True, account_matches=matches,
                verified_email=expected_email if matches else None,
                error=None if matches else 'The embedded browser is signed in to a different ChatGPT account.')
