"""Synthetic cookies only: scope, current-user decryption and native attributes."""
import base64
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from windows.brave_study_session import (
    CHROMIUM_EPOCH_SECONDS, StudyCookie, StudyCookieBatch, StudyCookieError,
    apply_to_cookie_manager, read_brave_chatgpt_cookies,
)

KEY = bytes(range(32))
SECRET = 'SYNTHETIC-SESSION-DO-NOT-LOG'
NOW = 1_800_000_000


@pytest.fixture
def brave(tmp_path):
    (tmp_path / 'Default/Network').mkdir(parents=True)
    (tmp_path / 'Local State').write_text(json.dumps(dict(
        profile={'last_used': 'Default'},
        os_crypt={'encrypted_key': base64.b64encode(b'DPAPIfixture-key').decode()})))
    database = tmp_path / 'Default/Network/Cookies'
    db = sqlite3.connect(database)
    db.execute('CREATE TABLE meta(key TEXT,value TEXT)')
    db.execute("INSERT INTO meta VALUES('version','24')")
    db.execute('''CREATE TABLE cookies(host_key TEXT,name TEXT,value TEXT,encrypted_value BLOB,
                  path TEXT,is_secure INTEGER,is_httponly INTEGER,expires_utc INTEGER,
                  has_expires INTEGER,samesite INTEGER,top_frame_site_key TEXT)''')
    db.commit()
    db.close()

    def add(domain='.chatgpt.com', value=SECRET, *, encrypted=None, expires=NOW+600,
            same_site=2, partition='', host_digest=None):
        nonce = b'123456789012'
        prefix = hashlib.sha256((host_digest or domain).encode()).digest()
        if encrypted is None:
            encrypted = b'v10' + nonce + AESGCM(KEY).encrypt(nonce, prefix + value.encode(), None)
        with sqlite3.connect(database) as conn:
            conn.execute('INSERT INTO cookies VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                         (domain, '__Secure-session', '', encrypted, '/', 1, 1,
                          int((expires + CHROMIUM_EPOCH_SECONDS)*1_000_000) if expires else 0,
                          int(expires is not None), same_site, partition))
    return tmp_path, database, add


def read(root):
    return read_brave_chatgpt_cookies(root, now=NOW, unprotect=lambda payload: KEY)


def test_scope_and_read_only_and_no_secret_in_repr(brave):
    root, db, add = brave
    add()
    add('bank.example', encrypted=b'v20-bank-secret')
    add('chatgpt.com.attacker.example', encrypted=b'v20-unrelated')
    before = db.read_bytes()
    batch = read(root)
    assert len(batch.cookies) == 1 and batch.cookies[0].value == SECRET
    assert batch.cookies[0].expires == NOW+600
    assert not batch.summary()['authenticated']
    assert SECRET not in repr(batch) + repr(batch.cookies) + json.dumps(batch.summary())
    assert db.read_bytes() == before


def test_expired_and_partitioned_skipped_before_decryption(brave):
    root, _, add = brave
    add()
    add(encrypted=b'v20-expired', expires=NOW-1)
    add(encrypted=b'v20-partitioned', partition='https://unrelated.example')
    batch = read(root)
    assert len(batch.cookies) == 1 and batch.expired == batch.partitioned == 1


@pytest.mark.parametrize('cipher', [b'v20-protected', b'v11-unsupported', b'v10truncated'])
def test_no_app_bound_bypass_or_partial_result(brave, cipher):
    root, _, add = brave
    add()
    add(encrypted=cipher)
    with pytest.raises(StudyCookieError) as error:
        read(root)
    assert SECRET not in str(error.value) and cipher.decode() not in str(error.value)


def test_host_digest_must_match(brave):
    root, _, add = brave
    add(host_digest='other.example')
    with pytest.raises(StudyCookieError):
        read(root)


def test_key_failure_is_redacted(brave):
    root, _, add = brave
    add()
    def unprotect(_):
        raise ValueError(SECRET)
    with pytest.raises(StudyCookieError) as error:
        read_brave_chatgpt_cookies(root, now=NOW, unprotect=unprotect)
    assert SECRET not in str(error.value)


def test_profile_traversal_rejected(brave):
    root, _, _ = brave
    (root/'Local State').write_text(json.dumps({'profile': {'last_used': '../outside'}}))
    with pytest.raises(StudyCookieError):
        read(root)


def test_session_expiry_preserved(brave):
    root, _, add = brave
    add(expires=None)
    assert read(root).cookies[0].expires is None


def test_old_database_without_host_digest(brave):
    root, db, add = brave
    nonce=b'123456789012'
    add(encrypted=b'v10'+nonce+AESGCM(KEY).encrypt(nonce, SECRET.encode(), None))
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE meta SET value='23'")
    assert read(root).cookies[0].value == SECRET


class FakeManager:
    def __init__(self):
        self.added=[]
    def CreateCookie(self, name, value, domain, path):
        return SimpleNamespace(name=name, value=value, domain=domain, path=path)
    def AddOrUpdateCookie(self, cookie):
        self.added.append(cookie)


def test_apply_preserves_attributes_without_claiming_signin():
    source=StudyCookie('.chatgpt.com','session',SECRET,'/auth',True,True,2,NOW+600)
    batch=StudyCookieBatch('Default',[source])
    manager=FakeManager()
    def options(cookie, original):
        cookie.options=(original.secure, original.http_only, original.same_site, original.expires)
    result=apply_to_cookie_manager(manager,batch,configure=options)
    assert result['success'] and result['imported']==1 and not result['authenticated']
    assert SECRET not in json.dumps(result)
    assert manager.added[0].path=='/auth'
    assert manager.added[0].options==(True,True,2,NOW+600)


def test_attribute_error_before_any_mutation():
    batch=StudyCookieBatch('Default',[StudyCookie('.chatgpt.com','a',SECRET),
                                    StudyCookie('bank.example','b',SECRET)])
    manager=FakeManager()
    result=apply_to_cookie_manager(manager,batch,configure=lambda *_: None)
    assert not result['success'] and result['imported']==0 and not manager.added


def test_native_failure_does_not_leak_values():
    class Failed(FakeManager):
        def AddOrUpdateCookie(self, cookie):
            raise RuntimeError(cookie.value)
    result=apply_to_cookie_manager(Failed(),StudyCookieBatch('Default',[StudyCookie('chatgpt.com','a',SECRET)]),configure=lambda *_: None)
    assert not result['success'] and SECRET not in json.dumps(result)
