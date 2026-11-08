import json
from windows.chatgpt_account_check import account_result

EXPECTED = 'mdaadhithyalok@gmail.com'


def test_only_verified_email_returned_never_session_tokens():
    result = account_result(200, 'application/json', json.dumps({
        'user': {'email': EXPECTED, 'id': 'private-user-id'},
        'accessToken': 'secret-token', 'refreshToken': 'secret-refresh',
    }), EXPECTED)
    assert result['success'] and result['authenticated'] and result['account_matches']
    assert result['verified_email'] == EXPECTED
    assert not any(secret in json.dumps(result) for secret in ('private-user-id', 'secret-token', 'secret-refresh'))


def test_different_account_redacted_and_does_not_pass():
    result = account_result(200, 'application/json', json.dumps({'user': {'email': 'other@example.com'}}), EXPECTED)
    assert result['authenticated'] and not result['success'] and not result['account_matches']
    assert 'other@example.com' not in json.dumps(result)


def test_challenge_empty_or_invalid_response_cannot_claim_signed_in():
    for status, kind, body in [(403, 'text/html', 'challenge'), (200, 'application/json', '{}'),
                              (200, 'application/json', 'not-json'), (200, 'text/html', EXPECTED)]:
        result = account_result(status, kind, body, EXPECTED)
        assert not result['success'] and not result['authenticated'] and not result['account_matches']
