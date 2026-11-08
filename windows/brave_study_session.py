"""User-requested Brave -> embedded ChatGPT cookie import, without an export file.

Only the current Windows user's legacy DPAPI-protected cookies are supported.
App-bound cookies are deliberately unsupported. No Brave files are modified, no
other site's records are selected, and secrets never enter reports or JS results.
"""
from dataclasses import dataclass, field
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time


DOMAINS = frozenset({
    'chatgpt.com', '.chatgpt.com', 'auth.openai.com', '.auth.openai.com',
    'openai.com', '.openai.com',
})
CHROMIUM_EPOCH_SECONDS = 11644473600


class StudyCookieError(Exception):
    """A fixed, safe-to-display message; never wrap a native secret-bearing error."""


@dataclass(frozen=True)
class StudyCookie:
    domain: str
    name: str = field(repr=False)
    value: str = field(repr=False)
    path: str = '/'
    secure: bool = True
    http_only: bool = True
    same_site: int = -1
    expires: float | None = None


@dataclass
class StudyCookieBatch:
    profile: str
    cookies: list[StudyCookie] = field(repr=False)
    expired: int = 0
    partitioned: int = 0

    def summary(self):
        return dict(source='Brave', profile=self.profile, eligible=len(self.cookies),
                    expired=self.expired, partitioned_skipped=self.partitioned,
                    authenticated=False)


def _unprotect_current_user(payload):
    import win32crypt
    # CRYPTPROTECT_UI_FORBIDDEN: no hidden password/security prompts.
    return win32crypt.CryptUnprotectData(payload, None, None, None, 1)[1]


def _master_key(state, unprotect):
    try:
        protected = base64.b64decode(state['os_crypt']['encrypted_key'], validate=True)
        if not protected.startswith(b'DPAPI'):
            raise ValueError()
        key = unprotect(protected[5:])
        if len(key) not in (16, 24, 32):
            raise ValueError()
        return key
    except Exception:
        raise StudyCookieError('Brave cookies could not be unlocked for this Windows user. Use embedded sign-in.') from None


def _decode_value(encrypted, clear, domain, version, get_key, unprotect):
    try:
        if not encrypted:
            return str(clear or '')
        if encrypted.startswith((b'v20', b'v11')):
            raise StudyCookieError('Brave uses unsupported cookie protection. Use embedded sign-in.')
        if encrypted.startswith(b'v10'):
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            if len(encrypted) < 31:
                raise ValueError()
            value = AESGCM(get_key()).decrypt(encrypted[3:15], encrypted[15:], None)
        else:
            value = unprotect(encrypted)
        if version >= 24:
            digest = hashlib.sha256(domain.encode('utf-8')).digest()
            if len(value) < 32 or not hmac.compare_digest(value[:32], digest):
                raise ValueError()
            value = value[32:]
        return value.decode('utf-8')
    except StudyCookieError:
        raise
    except Exception:
        raise StudyCookieError('A scoped Brave cookie could not be verified. No cookies were imported; use embedded sign-in.') from None


def read_brave_chatgpt_cookies(user_data=None, *, now=None, unprotect=None):
    """Read only selected domains in a read-only SQLite transaction, into memory."""
    root = Path(user_data) if user_data is not None else Path(os.environ['LOCALAPPDATA']) / 'BraveSoftware/Brave-Browser/User Data'
    try:
        state = json.loads((root / 'Local State').read_text(encoding='utf-8'))
        profile = state.get('profile', {}).get('last_used', 'Default')
        if not isinstance(profile, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 _-]{0,79}', profile):
            raise StudyCookieError('Brave has an unsupported profile directory. Use embedded sign-in.')
        profile_dir = (root / profile).resolve()
        if profile_dir.parent != root.resolve():
            raise StudyCookieError('Brave has an unsupported profile directory. Use embedded sign-in.')
        database = profile_dir / 'Network/Cookies'
        connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=2)
        try:
            connection.execute('PRAGMA query_only=ON')
            connection.row_factory = sqlite3.Row
            version = int(connection.execute("SELECT value FROM meta WHERE key='version'").fetchone()[0])
            columns = {row[1] for row in connection.execute('PRAGMA table_info(cookies)')}
            partition = 'top_frame_site_key' if 'top_frame_site_key' in columns else "'' AS top_frame_site_key"
            placeholders = ','.join('?' for _ in DOMAINS)
            # Values for banking, payment, mail, or any unrelated domain never
            # leave SQLite. Do not replace this with SELECT * or a DB export.
            rows = connection.execute(
                'SELECT host_key,name,value,encrypted_value,path,is_secure,is_httponly,'
                f'expires_utc,has_expires,samesite,{partition} FROM cookies '
                f'WHERE host_key IN ({placeholders})', tuple(sorted(DOMAINS))).fetchall()
        finally:
            connection.close()
    except StudyCookieError:
        raise
    except Exception:
        raise StudyCookieError('Brave\'s active cookie database is unavailable. Close Brave briefly or use embedded sign-in.') from None

    result = StudyCookieBatch(profile, [])
    clock = time.time() if now is None else now
    unprotect = unprotect or _unprotect_current_user
    key = None

    def get_key():
        nonlocal key
        if key is None:
            key = _master_key(state, unprotect)
        return key

    for row in rows:
        expires = int(row['expires_utc']) / 1_000_000 - CHROMIUM_EPOCH_SECONDS if row['has_expires'] else None
        if expires is not None and expires <= clock:
            result.expired += 1
            continue
        if row['top_frame_site_key']:
            result.partitioned += 1
            continue  # WebView2 CookieManager cannot preserve the partition key.
        value = _decode_value(bytes(row['encrypted_value']), row['value'], row['host_key'], version, get_key, unprotect)
        result.cookies.append(StudyCookie(row['host_key'], row['name'], value,
                                         row['path'], bool(row['is_secure']), bool(row['is_httponly']),
                                         int(row['samesite']), expires))
    if not result.cookies:
        raise StudyCookieError('No eligible ChatGPT cookies were found in Brave\'s active profile. Sign in there or use embedded sign-in.')
    return result


def _net_cookie_options(cookie, source):
    from Microsoft.Web.WebView2.Core import CoreWebView2CookieSameSiteKind
    from System import DateTime, DateTimeKind
    cookie.IsSecure = source.secure
    cookie.IsHttpOnly = source.http_only
    cookie.SameSite = {
        -1: CoreWebView2CookieSameSiteKind.Lax,
        0: getattr(CoreWebView2CookieSameSiteKind, 'None'),
        1: CoreWebView2CookieSameSiteKind.Lax,
        2: CoreWebView2CookieSameSiteKind.Strict,
    }[source.same_site]
    cookie.Expires = (DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc).AddSeconds(source.expires)
                      if source.expires is not None else DateTime.MinValue)


def apply_to_cookie_manager(manager, batch, *, configure=None):
    """Must run on the owning WebView2 thread. Never deletes any existing cookie."""
    configure = configure or _net_cookie_options
    prepared = []
    try:
        for source in batch.cookies:
            if source.domain not in DOMAINS or source.same_site not in {-1, 0, 1, 2}:
                raise ValueError()
            cookie = manager.CreateCookie(source.name, source.value, source.domain, source.path)
            configure(cookie, source)
            prepared.append(cookie)
    except Exception:
        return dict(batch.summary(), success=False, imported=0,
                    error='WebView2 could not preserve these cookie attributes. No cookies were imported.')
    imported = 0
    try:
        for cookie in prepared:
            manager.AddOrUpdateCookie(cookie)
            imported += 1
    except Exception:
        return dict(batch.summary(), success=False, imported=imported,
                    error='WebView2 stopped accepting cookies. Open ChatGPT to check sign-in.')
    return dict(batch.summary(), success=True, imported=imported,
                message='Brave cookies imported. ChatGPT must still confirm this session.')


def import_into_desktop(native, *, timeout=15):
    """Worker entry point; only secret-free metadata returns to the caller."""
    try:
        batch = read_brave_chatgpt_cookies()
    except StudyCookieError as error:
        return {'success': False, 'imported': 0, 'error': str(error)}
    done = threading.Event()
    result = {}

    def apply():
        try:
            from windows.webview_lifecycle import find_webview_on_ui_thread
            control = find_webview_on_ui_thread(native)
            result.update(apply_to_cookie_manager(control.CoreWebView2.CookieManager, batch))
        except Exception:
            result.update(success=False, imported=0, error='The embedded browser is not ready. Try again after the dashboard loads.')
        finally:
            batch.cookies.clear()
            done.set()

    from System import Action
    from windows.webview_lifecycle import post_to_ui
    if not post_to_ui(native, apply, Action):
        batch.cookies.clear()
        return {'success': False, 'imported': 0, 'error': 'The embedded browser is unavailable.'}
    if not done.wait(timeout):
        # Do not expose or reuse the batch. The queued UI operation owns it until
        # completion; a pending operation is not reported as a successful import.
        return {'success': False, 'pending': True, 'error': 'Cookie import is still waiting for the desktop browser.'}
    return result
