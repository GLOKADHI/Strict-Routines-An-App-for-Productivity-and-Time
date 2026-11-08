"""Bounded versioned framing shared by the existing RFCOMM transfer endpoints."""
import hashlib
import hmac
import json
import re
import struct

PROTOCOL = 'STRICTROUTINES_BT'
VERSION = 2
MAX_TRANSFER_BYTES = 16 * 1024 * 1024
MAX_FRAME_BYTES = 16384
MAX_RECORDS = 10000


def strict_json(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError('Duplicate JSON object key')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite JSON value')))
    except RecursionError as error:
        raise ValueError('JSON nesting exceeds the supported schema') from error


def read_exact(stream, count, progress=None):
    if type(count) is not int or not 0 <= count <= MAX_TRANSFER_BYTES:
        raise ValueError('Invalid transfer length')
    data = bytearray()
    while len(data) < count:
        chunk = stream.recv(min(16384, count - len(data)))
        if not chunk:
            raise ConnectionError('Incomplete Bluetooth transmission; retry the original package')
        data.extend(chunk)
        if progress:
            progress(len(data), count)
    return bytes(data)


def read_frame(stream):
    size = struct.unpack('!I', read_exact(stream, 4))[0]
    if not 0 < size <= MAX_FRAME_BYTES:
        raise ValueError('Unsupported or oversized Bluetooth message')
    value = strict_json(read_exact(stream, size).decode('utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Bluetooth message must be an object')
    return value


def send_frame(stream, value):
    raw = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(raw) > MAX_FRAME_BYTES:
        raise ValueError('Bluetooth response exceeds the frame limit')
    stream.sendall(struct.pack('!I', len(raw)) + raw)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.:@-]{1,128}', value):
        raise ValueError('Invalid transfer identity')
    return value


def auth_text(session_id, challenge, package_id, device_id, account_id, size, count):
    for value in (session_id, challenge, package_id, device_id, account_id):
        identifier(value)
    return '\n'.join(map(str, (session_id, challenge, package_id, device_id, account_id, size, count)))


def mac(code, text):
    return hmac.new(code.encode('utf-8'), text.encode('utf-8'), hashlib.sha256).hexdigest()


def receipt_text(receipt):
    keys = ('protocol', 'version', 'session_id', 'package_id', 'success', 'scoring_date',
            'confirmed_score', 'total_count', 'transferred_count', 'already_present_count',
            'rejected_count', 'conflict_count', 'ledger_sha256', 'synced_at')
    return '\n'.join('true' if receipt.get(key) is True else 'false' if receipt.get(key) is False
                     else str(receipt.get(key, '')) for key in keys)


def package_count(payload):
    ledger = strict_json(payload['ledger_snapshot']['payload_json'])
    return len(ledger['score_events']) + len(payload.get('proof_reviews', [])) + len(payload.get('scheduled_activities', [])) + len(payload.get('planning_requests', []))
