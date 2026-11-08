"""StrictRecords — Cloudflare Realtime TURN Service.

Generates short-lived, session-scoped WebRTC ICE credentials using Cloudflare
Realtime TURN (https://developers.cloudflare.com/realtime/turn/).

Permanent credentials (TURN_KEY_ID, TURN_KEY_API_TOKEN) are stored exclusively
on the backend and are NEVER exposed to mobile devices, frontend JavaScript,
or public API responses.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger("CloudflareTURN")

CLOUDFLARE_TURN_ENDPOINT = "https://rtc.live.cloudflare.com/v1/turn/keys/{key_id}/credentials/generate-ice-servers"

DEFAULT_STUN_SERVERS = [
    "stun:stun.cloudflare.com:3478",
    "stun:stun.l.google.com:19302",
]


class CloudflareTURNService:
    """Manages Cloudflare Realtime TURN keys and temporary credential generation."""

    _instance: Optional[CloudflareTURNService] = None
    _instance_lock = threading.Lock()

    def __init__(self, db=None):
        self.db = db
        self.lock = threading.Lock()
        self._cached_credentials: Dict[str, Any] = {}
        self._last_error = ""

    @classmethod
    def get_instance(cls, db=None) -> CloudflareTURNService:
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(db=db)
            elif db is not None and cls._instance.db is None:
                cls._instance.db = db
            return cls._instance

    def _get_setting(self, key: str, default: str = "") -> str:
        # 1. Environment variable override
        val = os.environ.get(key, "").strip()
        if val:
            return val
        # 2. Database system_config table
        if self.db:
            try:
                with self.db._connect() as conn:
                    row = conn.execute("SELECT value FROM system_config WHERE key=?", (key,)).fetchone()
                    if row and row["value"]:
                        return str(row["value"]).strip()
            except Exception as e:
                logger.debug(f"Error querying {key} from system_config: {e}")
        return default

    def _set_setting(self, key: str, value: str) -> None:
        if self.db:
            try:
                now_iso = time.strftime("%Y-%m-%d %H:%M:%S")
                with self.db._connect() as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO system_config (key, value, updated_at) VALUES (?, ?, ?)",
                        (key, value, now_iso),
                    )
            except Exception as e:
                logger.error(f"Error saving {key} to system_config: {e}")

    @property
    def key_id(self) -> str:
        return self._get_setting("CLOUDFLARE_TURN_KEY_ID") or self._get_setting("TURN_KEY_ID")

    @property
    def api_token(self) -> str:
        return self._get_setting("CLOUDFLARE_TURN_API_TOKEN") or self._get_setting("TURN_KEY_API_TOKEN")

    def configure_credentials(self, key_id: str, api_token: str) -> Dict[str, Any]:
        """Save permanent TURN API credentials to backend database."""
        key_id = key_id.strip() if key_id else ""
        api_token = api_token.strip() if api_token else ""
        with self.lock:
            if key_id:
                self._set_setting("CLOUDFLARE_TURN_KEY_ID", key_id)
            if api_token:
                self._set_setting("CLOUDFLARE_TURN_API_TOKEN", api_token)
            self._cached_credentials.clear()
            self._last_error = ""

        # Validate by generating a probe credential
        valid, err = self.test_connection()
        return {
            "success": valid,
            "configured": bool(key_id and api_token),
            "key_id": key_id[:6] + "..." if len(key_id) > 6 else key_id,
            "error": err,
        }

    def get_status(self) -> Dict[str, Any]:
        kid = self.key_id
        tok = self.api_token
        return {
            "configured": bool(kid and tok),
            "key_id_masked": f"{kid[:6]}...{kid[-4:]}" if len(kid) > 10 else ("configured" if kid else "not_configured"),
            "token_masked": bool(tok),
            "last_error": self._last_error,
            "stun_fallback_available": True,
        }

    def test_connection(self) -> tuple[bool, str]:
        """Validates Cloudflare TURN credentials by requesting a short-lived token."""
        kid = self.key_id
        tok = self.api_token
        if not kid or not tok:
            return False, "Cloudflare TURN key_id or api_token is not configured."
        try:
            ice = self.generate_ice_servers(ttl_seconds=300, force_refresh=True)
            has_turn = any(
                any(u.startswith(("turn:", "turns:")) for u in server.get("urls", []))
                for server in ice
            )
            if has_turn:
                self._last_error = ""
                return True, "Cloudflare TURN credentials generated successfully."
            return False, "Cloudflare response did not include TURN servers."
        except Exception as e:
            msg = str(e)
            self._last_error = msg
            return False, msg

    def generate_ice_servers(
        self,
        session_id: str = "",
        camera_id: str = "",
        ttl_seconds: int = 3600,
        force_refresh: bool = False,
    ) -> List[Dict[str, Any]]:
        """Generates temporary, short-lived ICE server configuration.

        Returns a list of RTCIceServer dictionaries suitable for RTCPeerConnection:
        [
            {"urls": ["stun:stun.cloudflare.com:3478", ...]},
            {
                "urls": [
                    "turn:turn.cloudflare.com:3478?transport=udp",
                    "turn:turn.cloudflare.com:3478?transport=tcp",
                    "turns:turn.cloudflare.com:443?transport=tcp",
                    "turns:turn.cloudflare.com:5349?transport=tcp",
                ],
                "username": "<ephemeral_user>",
                "credential": "<ephemeral_password>",
            }
        ]
        """
        ttl_seconds = max(300, min(86400, int(ttl_seconds)))
        kid = self.key_id
        tok = self.api_token

        # If credentials are not configured, return standard STUN servers
        if not kid or not tok:
            logger.debug("Cloudflare TURN keys not configured; using standard STUN.")
            return [{"urls": DEFAULT_STUN_SERVERS}]

        cache_key = f"{session_id}:{camera_id}"
        now = time.time()
        with self.lock:
            cached = self._cached_credentials.get(cache_key)
            if not force_refresh and cached and cached["expires_at"] > now + 60:
                return cached["ice_servers"]

        endpoint = CLOUDFLARE_TURN_ENDPOINT.format(key_id=kid)
        req_body = json.dumps({"ttl": ttl_seconds}).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=req_body,
            headers={
                "Authorization": f"Bearer {tok}",
                "Content-Type": "application/json",
                "User-Agent": "StrictRecords-WebRTC/1.0",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            parsed_ice = self._parse_cloudflare_ice(data)
            with self.lock:
                self._cached_credentials[cache_key] = {
                    "ice_servers": parsed_ice,
                    "expires_at": now + ttl_seconds,
                }
                self._last_error = ""
            logger.info("Successfully generated ephemeral Cloudflare TURN credentials.")
            return parsed_ice

        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode("utf-8")
            except Exception:
                err_body = str(e)
            self._last_error = f"HTTP {e.code}: {err_body}"
            logger.warning(f"Cloudflare TURN API error ({e.code}): {err_body}. Falling back to STUN.")
            return [{"urls": DEFAULT_STUN_SERVERS}]
        except Exception as e:
            self._last_error = str(e)
            logger.warning(f"Cloudflare TURN connection failed: {e}. Falling back to STUN.")
            return [{"urls": DEFAULT_STUN_SERVERS}]

    def _parse_cloudflare_ice(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Parses Cloudflare TURN response into standard RTCIceServer list."""
        ice_servers: List[Dict[str, Any]] = []

        # Cloudflare Calls API format:
        # { "iceServers": { "urls": [...], "username": "...", "credential": "..." } }
        # or array format:
        # { "iceServers": [ { "urls": [...], "username": "...", "credential": "..." } ] }
        raw = data.get("iceServers") or data.get("result", {}).get("iceServers") or data

        if isinstance(raw, dict):
            urls = raw.get("urls") or []
            if isinstance(urls, str):
                urls = [urls]

            # Separate STUN from TURN so browsers handle STUN first
            stun_urls = [u for u in urls if u.startswith("stun:")]
            turn_urls = [u for u in urls if u.startswith(("turn:", "turns:"))]

            if not stun_urls:
                stun_urls = list(DEFAULT_STUN_SERVERS)

            ice_servers.append({"urls": stun_urls})

            if turn_urls and raw.get("username") and raw.get("credential"):
                # Ensure TURN over TLS on port 443 is present if Cloudflare provides turns
                has_tls_443 = any("443" in u for u in turn_urls)
                if not has_tls_443:
                    turn_urls.append("turns:turn.cloudflare.com:443?transport=tcp")

                ice_servers.append({
                    "urls": turn_urls,
                    "username": raw["username"],
                    "credential": raw["credential"],
                })

        elif isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict) and "urls" in item:
                    ice_servers.append(item)

        if not ice_servers:
            ice_servers.append({"urls": DEFAULT_STUN_SERVERS})

        return ice_servers
