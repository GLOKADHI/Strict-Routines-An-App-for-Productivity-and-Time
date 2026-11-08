import hashlib
import io
import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from PIL import Image
from core.server import StrictRecordsRequestHandler


@pytest.fixture
def receipt_handler(tmp_path):
    store = tmp_path / "retained"
    store.mkdir()
    output = io.BytesIO()
    Image.new("RGB", (120, 120), "green").save(output, "PNG")
    image = output.getvalue()
    path = store / "evidence.png"
    path.write_bytes(image)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE evidence_files(evidence_id TEXT, file_path TEXT, sha256_hash TEXT, is_deleted INTEGER);
        CREATE TABLE proof_reviews(evidence_id TEXT, user_id TEXT);
    """)
    conn.execute("INSERT INTO evidence_files VALUES ('ev_test',?,?,0)", (str(path), hashlib.sha256(image).hexdigest()))
    conn.execute("INSERT INTO proof_reviews VALUES ('ev_test','account_one')")
    @contextmanager
    def connect():
        yield conn
    handler = object.__new__(StrictRecordsRequestHandler)
    handler.db = SimpleNamespace(_connect=connect)
    handler.engine = SimpleNamespace(storage_root=str(store))
    handler.headers = {"Authorization": "Bearer device-secret"}
    def authenticate(device, token):
        if device != "phone" or token != "device-secret":
            raise PermissionError()
        return "account_one"
    handler._get_authenticated_device_account = authenticate
    results = []
    handler._send_json = lambda data, status=200: results.append(status)
    handler.send_response = lambda status: results.append(status)
    handler.send_header = lambda *args: None
    handler.end_headers = lambda: None
    handler.wfile = io.BytesIO()
    yield handler, conn, path, results, image
    conn.close()


def request(handler):
    handler._handle_device_proof_evidence({"device_id": ["phone"], "evidence_id": ["ev_test"]})


def test_authenticated_owner_receives_original_bytes_without_mutation(receipt_handler):
    handler, conn, _, result, original = receipt_handler
    before = list(conn.iterdump())
    request(handler)
    assert result == [200]
    assert handler.wfile.getvalue() == original
    assert list(conn.iterdump()) == before


@pytest.mark.parametrize("failure", ["unauthorized", "other_account", "deleted", "missing", "corrupt", "hash_mismatch", "outside_storage"])
def test_unavailable_or_unauthorized_evidence_is_never_exposed(receipt_handler, tmp_path, failure):
    handler, conn, path, result, _ = receipt_handler
    if failure == "unauthorized":
        handler.headers = {}
    elif failure == "other_account":
        conn.execute("UPDATE proof_reviews SET user_id='another_account'")
    elif failure == "deleted":
        conn.execute("UPDATE evidence_files SET is_deleted=1")
    elif failure == "missing":
        path.unlink()
    elif failure == "corrupt":
        path.write_bytes(b"not an image")
    elif failure == "hash_mismatch":
        conn.execute("UPDATE evidence_files SET sha256_hash='wrong'")
    else:
        outside = tmp_path / "outside.png"
        outside.write_bytes(path.read_bytes())
        conn.execute("UPDATE evidence_files SET file_path=?", (str(outside),))
    request(handler)
    assert result[0] in (403, 404, 422)
    assert handler.wfile.getvalue() == b""
