"""StrictRecords — Cloudflare Secure HTTPS Tunnel Manager.

Manages the lifecycle of cloudflared (Cloudflare Tunnel) to expose the local
mobile camera gateway ingress (http://127.0.0.1:9443) via trusted public HTTPS.

Supports two operational modes:
1. QUICK: Temporary Cloudflare Quick Tunnel (development/on-demand).
   Runs: cloudflared.exe tunnel --url http://127.0.0.1:9443
   Parses ephemeral trycloudflare.com hostname from stderr and verifies HTTPS.
2. NAMED: Persistent named tunnel with custom domain (production).
   Runs: cloudflared.exe tunnel run --token <token> or checks Windows service.
   Verifies public HTTPS connectivity against the configured domain.

Monitors tunnel process health, executes real HTTPS connectivity probes,
and never mocks or fakes connectivity status.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("CloudflareTunnel")

CLOUDFLARE_TRY_URL_REGEX = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", re.IGNORECASE)


def find_cloudflared_binary() -> Optional[str]:
    """Locates the standalone cloudflared executable."""
    candidates = [
        # 1. Project bin directory
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "cloudflared.exe"),
        # 2. LocalAppData cloudflared directory
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "cloudflared", "cloudflared.exe"),
        # 3. Project root
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cloudflared.exe"),
        # 4. Program Files
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "cloudflared", "cloudflared.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "cloudflared", "cloudflared.exe"),
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return os.path.abspath(c)

    # 5. System PATH lookup
    path_bin = shutil.which("cloudflared") or shutil.which("cloudflared.exe")
    if path_bin:
        return os.path.abspath(path_bin)

    return None


class CloudflareTunnelManager:
    """Singleton manager for the Cloudflare Tunnel process."""

    _instance: Optional[CloudflareTunnelManager] = None
    _instance_lock = threading.Lock()

    def __init__(self, db=None, local_port: int = 9443):
        self.db = db
        self.local_port = local_port
        self.lock = threading.Lock()

        self._process: Optional[subprocess.Popen] = None
        self._watchdog_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        self._mode = "QUICK"  # "QUICK" or "NAMED"
        self._public_origin = ""
        self._status = "STOPPED"  # STOPPED, STARTING, CONNECTED, DEGRADED, ERROR
        self._last_error = ""
        self._last_probe_time = 0.0
        self._last_probe_status = False
        self._consecutive_probe_failures = 0
        self._restart_backoff = 2.0
        self._on_origin_updated = None

        self._load_saved_config()

    def set_on_origin_updated(self, callback) -> None:
        """Registers a callback invoked whenever the public origin is discovered or changed."""
        self._on_origin_updated = callback

    @classmethod
    def get_instance(cls, db=None, local_port: int = 9443) -> CloudflareTunnelManager:
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(db=db, local_port=local_port)
            elif db is not None and cls._instance.db is None:
                cls._instance.db = db
            return cls._instance

    def _get_setting(self, key: str, default: str = "") -> str:
        val = os.environ.get(key, "").strip()
        if val:
            return val
        if self.db:
            try:
                with self.db._connect() as conn:
                    cols = [r[1] for r in conn.execute("PRAGMA table_info(system_config)").fetchall()]
                    key_col = "config_key" if "config_key" in cols else "key"
                    val_col = "config_value" if "config_value" in cols else "value"
                    row = conn.execute(f"SELECT {val_col} FROM system_config WHERE {key_col}=?", (key,)).fetchone()
                    if row and row[0]:
                        return str(row[0]).strip()
            except Exception as e:
                logger.debug(f"Error querying {key} from system_config: {e}")
        return default

    def _set_setting(self, key: str, value: str) -> None:
        if self.db:
            try:
                now_iso = time.strftime("%Y-%m-%d %H:%M:%S")
                with self.db._connect() as conn:
                    cols = [r[1] for r in conn.execute("PRAGMA table_info(system_config)").fetchall()]
                    key_col = "config_key" if "config_key" in cols else "key"
                    val_col = "config_value" if "config_value" in cols else "value"
                    conn.execute(
                        f"INSERT OR REPLACE INTO system_config ({key_col}, {val_col}, updated_at) VALUES (?, ?, ?)",
                        (key, value, now_iso),
                    )
            except Exception as e:
                logger.error(f"Error saving {key} to system_config: {e}")

    def _load_saved_config(self) -> None:
        saved_mode = self._get_setting("CLOUDFLARE_TUNNEL_MODE", "QUICK").upper()
        if saved_mode in ("QUICK", "NAMED"):
            self._mode = saved_mode
        else:
            self._mode = "QUICK"

        saved_origin = self._get_setting("CLOUDFLARE_PUBLIC_HOSTNAME", "")
        if saved_origin and not saved_origin.startswith("http"):
            saved_origin = "https://" + saved_origin
        if self._mode == "NAMED" and saved_origin:
            self._public_origin = saved_origin
        else:
            self._public_origin = ""

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def public_origin(self) -> str:
        return self._public_origin

    @property
    def status(self) -> str:
        return self._status

    def configure(
        self,
        mode: Optional[str] = None,
        public_hostname: Optional[str] = None,
        tunnel_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Updates tunnel settings in database and restarts if necessary."""
        with self.lock:
            reconnect_needed = False
            if mode is not None:
                mode_clean = mode.strip().upper()
                if mode_clean in ("QUICK", "NAMED") and mode_clean != self._mode:
                    self._mode = mode_clean
                    self._set_setting("CLOUDFLARE_TUNNEL_MODE", self._mode)
                    reconnect_needed = True

            if public_hostname is not None:
                host_clean = public_hostname.strip().lower()
                if host_clean.startswith("http://"):
                    host_clean = "https://" + host_clean[7:]
                elif host_clean and not host_clean.startswith("https://"):
                    host_clean = "https://" + host_clean
                self._set_setting("CLOUDFLARE_PUBLIC_HOSTNAME", host_clean)
                if self._mode == "NAMED":
                    self._public_origin = host_clean
                    reconnect_needed = True

            if tunnel_token is not None:
                tok_clean = tunnel_token.strip()
                if tok_clean:
                    self._set_setting("CLOUDFLARE_TUNNEL_TOKEN", tok_clean)
                    if self._mode == "NAMED":
                        reconnect_needed = True

        if reconnect_needed and self.is_running():
            logger.info("Configuration changed; restarting Cloudflare tunnel...")
            self.restart_tunnel()

        return self.get_status()

    def get_status(self) -> Dict[str, Any]:
        """Returns comprehensive real-time status of the Cloudflare tunnel."""
        with self.lock:
            bin_path = find_cloudflared_binary()
            token_saved = bool(self._get_setting("CLOUDFLARE_TUNNEL_TOKEN", ""))
            return {
                "binary_available": bool(bin_path),
                "binary_path": bin_path or "not_found",
                "mode": self._mode,
                "status": self._status,
                "public_origin": self._public_origin,
                "tunnel_connected": self._status == "CONNECTED",
                "https_origin_reachable": self._last_probe_status,
                "last_probe_time": self._last_probe_time,
                "last_error": self._last_error,
                "is_running": self.is_running(),
                "token_configured": token_saved,
                "local_target": f"http://127.0.0.1:{self.local_port}",
            }

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start_tunnel(self) -> bool:
        """Launches the Cloudflare tunnel in the configured mode."""
        with self.lock:
            if self.is_running():
                logger.info("Cloudflare tunnel is already running.")
                return True

            bin_path = find_cloudflared_binary()
            if not bin_path:
                self._status = "ERROR"
                self._last_error = "cloudflared.exe binary was not found on this system."
                logger.error(self._last_error)
                return False

            self._stop_event.clear()
            self._status = "STARTING"
            self._last_error = ""
            if self._mode == "QUICK":
                self._public_origin = ""

            try:
                if self._mode == "QUICK":
                    cmd = [
                        bin_path,
                        "tunnel",
                        "--url",
                        f"http://127.0.0.1:{self.local_port}",
                        "--no-autoupdate",
                    ]
                    logger.info(f"Starting Cloudflare Quick Tunnel: {' '.join(cmd)}")
                else:
                    token = self._get_setting("CLOUDFLARE_TUNNEL_TOKEN")
                    if not token:
                        self._status = "ERROR"
                        self._last_error = "Named tunnel selected but CLOUDFLARE_TUNNEL_TOKEN is not configured."
                        logger.error(self._last_error)
                        return False
                    cmd = [
                        bin_path,
                        "tunnel",
                        "run",
                        "--token",
                        token,
                    ]
                    logger.info("Starting Cloudflare Named Tunnel with configured token")

                # Launch cloudflared with stderr redirected for URL parsing & logging
                creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                self._process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    creationflags=creationflags,
                )

                # Start background watchdog and output reader
                self._watchdog_thread = threading.Thread(
                    target=self._watchdog_loop,
                    name="CloudflareTunnelWatchdog",
                    daemon=True,
                )
                self._watchdog_thread.start()
                return True

            except Exception as e:
                self._status = "ERROR"
                self._last_error = f"Failed to spawn cloudflared: {e}"
                logger.exception(self._last_error)
                return False

    def stop_tunnel(self) -> None:
        """Stops the Cloudflare tunnel process cleanly."""
        self._stop_event.set()
        proc = None
        with self.lock:
            proc = self._process
            self._process = None
            self._status = "STOPPED"

        if proc and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except Exception as e:
                logger.debug(f"Error terminating cloudflared: {e}")

        logger.info("Cloudflare tunnel stopped.")

    def restart_tunnel(self) -> bool:
        """Stops and immediately restarts the tunnel."""
        self.stop_tunnel()
        time.sleep(0.5)
        return self.start_tunnel()

    def _watchdog_loop(self) -> None:
        """Monitors cloudflared stderr for quick tunnel URL and tracks process health."""
        proc = self._process
        if not proc or not proc.stderr:
            return

        def read_stderr():
            try:
                for line in iter(proc.stderr.readline, ""):
                    if self._stop_event.is_set():
                        break
                    line_s = line.strip()
                    if not line_s:
                        continue

                    # Look for Quick Tunnel URL
                    if self._mode == "QUICK":
                        match = CLOUDFLARE_TRY_URL_REGEX.search(line_s)
                        if match:
                            found_url = match.group(0)
                            if found_url != self._public_origin:
                                with self.lock:
                                    self._public_origin = found_url
                                logger.info(f"Cloudflare Quick Tunnel established: {found_url}")
                                if callable(self._on_origin_updated):
                                    try:
                                        self._on_origin_updated(found_url)
                                    except Exception as _cb_err:
                                        logger.warning(f"Error invoking origin updated callback: {_cb_err}")
                                # Immediately probe health
                                def _delayed_probe():
                                    time.sleep(2.0)
                                    self._probe_public_https()
                                threading.Thread(target=_delayed_probe, daemon=True, name="CF_InitialProbe").start()

                    if "ERR" in line_s or "error" in line_s.lower():
                        logger.warning(f"[cloudflared] {line_s}")
                    else:
                        logger.debug(f"[cloudflared] {line_s}")
            except Exception as ex:
                logger.debug(f"Stderr reader exception: {ex}")

        reader_thread = threading.Thread(target=read_stderr, daemon=True, name="CF_StderrReader")
        reader_thread.start()

        # Watchdog loop
        while not self._stop_event.is_set():
            ret = proc.poll()
            if ret is not None:
                # Process exited unexpectedly
                with self.lock:
                    self._status = "ERROR" if ret != 0 else "STOPPED"
                    self._last_error = f"cloudflared exited with code {ret}"
                    self._process = None
                logger.error(f"Cloudflare tunnel exited unexpectedly: code {ret}")
                break

            # Periodic probe every 15 seconds
            now = time.time()
            if now - self._last_probe_time > 15:
                self._probe_public_https()

            time.sleep(1.0)

    def _probe_public_https(self) -> bool:
        """Executes a real HTTPS GET against the public tunnel origin to verify connectivity."""
        origin = self._public_origin
        if not origin:
            return False

        probe_url = f"{origin.rstrip('/')}/ios-camera/health"
        self._last_probe_time = time.time()
        try:
            req = urllib.request.Request(
                probe_url,
                headers={"User-Agent": "StrictRecords-Cloudflare-Probe/1.0"},
            )
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if resp.status == 200 and data.get("success"):
                    with self.lock:
                        self._last_probe_status = True
                        self._status = "CONNECTED"
                        self._consecutive_probe_failures = 0
                        self._last_error = ""
                    logger.info(f"Cloudflare HTTPS probe SUCCESS: {probe_url}")
                    return True
                else:
                    raise ValueError(f"Unexpected probe response: {data}")
        except Exception as e:
            with self.lock:
                self._last_probe_status = False
                self._consecutive_probe_failures += 1
                if self._consecutive_probe_failures >= 3:
                    self._status = "DEGRADED"
                self._last_error = f"HTTPS probe failed: {e}"
            logger.warning(f"Cloudflare HTTPS probe failed ({probe_url}): {e}")
            return False
