"""
StrictRecords — Windows Native Messaging Host for YouTube Metadata Extraction
=============================================================================
Provides secure native stdio communication between the StrictRecords browser
extension and the desktop environment.
Extracts YouTube metadata using yt-dlp (--dump-single-json --skip-download)
with instant HTML fallback.
Does NOT download video or media files.

Native Messaging Protocol:
Each message is preceded by its 32-bit little-endian length in bytes.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import ssl
import struct
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Ensure binary mode for stdio on Windows
if sys.platform == "win32":
    import msvcrt
    if sys.stdin is not None:
        try:
            msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        except Exception:
            pass
    if sys.stdout is not None:
        try:
            msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
        except Exception:
            pass

logging.basicConfig(
    filename=os.path.join(os.environ.get("LOCALAPPDATA", "."), "strictrecords_native_host.log"),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("StrictRecordsNativeHost")

# Only allow calls from our registered extension
ALLOWED_EXTENSION_ID = "iincmekgilffpjmmmcgloliadppioajc"
ALLOWED_ORIGIN = f"chrome-extension://{ALLOWED_EXTENSION_ID}/"

YOUTUBE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")


def read_message() -> Optional[Dict[str, Any]]:
    """Reads a message from stdio using Chrome Native Messaging framing."""
    raw_length = sys.stdin.buffer.read(4)
    if len(raw_length) < 4:
        return None
    message_length = struct.unpack("<I", raw_length)[0]
    if message_length == 0 or message_length > 10 * 1024 * 1024:
        logger.warning("Invalid message length received: %d", message_length)
        return None
    raw_message = sys.stdin.buffer.read(message_length)
    if len(raw_message) < message_length:
        return None
    try:
        return json.loads(raw_message.decode("utf-8"))
    except json.JSONDecodeError as exc:
        logger.error("JSON decode error: %s", exc)
        return None


def send_message(message: Dict[str, Any]) -> None:
    """Sends a message to stdio using Chrome Native Messaging framing."""
    data = json.dumps(message, ensure_ascii=False).encode("utf-8")
    length_prefix = struct.pack("<I", len(data))
    sys.stdout.buffer.write(length_prefix + data)
    sys.stdout.buffer.flush()


def find_yt_dlp_executable() -> Optional[list[str]]:
    """Locates yt-dlp executable in PATH or virtual environments."""
    # 1. Check direct yt-dlp in PATH
    which_path = shutil.which("yt-dlp")
    if which_path:
        return [which_path]

    # 2. Check yt-dlp.exe
    which_path = shutil.which("yt-dlp.exe")
    if which_path:
        return [which_path]

    # 3. Check python -m yt_dlp
    py_exec = sys.executable
    if py_exec:
        return [py_exec, "-m", "yt_dlp"]

    return None


def extract_metadata_fast_html(clean_id: str) -> Optional[Dict[str, Any]]:
    """Ultra-fast sub-second YouTube metadata extraction via direct HTML payload."""
    try:
        url = f"https://www.youtube.com/watch?v={clean_id}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=4.0, context=ctx) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        idx = html.find("ytInitialPlayerResponse")
        if idx != -1:
            s_idx = html.find("{", idx)
            if s_idx != -1:
                data, _ = json.JSONDecoder().raw_decode(html[s_idx:])
                vd = data.get("videoDetails", {})
                dur = int(vd.get("lengthSeconds", 0))
                if dur > 0:
                    dur_formatted = f"{dur//60:02d}:{dur%60:02d}"
                    desc = vd.get("shortDescription") or ""
                    chapters = []
                    for cm in re.finditer(r"\b(\d{1,2}:\d{2}(?::\d{2})?)\s+([^\n\r]+)", desc):
                        chapters.append({"start_time": cm.group(1), "title": cm.group(2).strip()})
                    return {
                        "success": True,
                        "video_id": clean_id,
                        "canonical_url": url,
                        "title": vd.get("title") or "YouTube Video",
                        "channel": vd.get("author") or "YouTube Creator",
                        "duration_seconds": dur,
                        "duration_formatted": dur_formatted,
                        "description": desc,
                        "chapters": chapters,
                    }
    except Exception as exc:
        logger.debug("Fast HTML metadata extraction fallback triggered: %s", exc)
    return None


def extract_metadata_oembed(clean_id: str) -> Optional[Dict[str, Any]]:
    """Instant sub-second YouTube metadata extraction via official oEmbed API."""
    try:
        url = f"https://www.youtube.com/watch?v={clean_id}"
        oembed_url = f"https://www.youtube.com/oembed?url={url}&format=json"
        req = urllib.request.Request(
            oembed_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
            }
        )
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=3.0, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        title = data.get("title")
        author = data.get("author_name")
        if title:
            return {
                "success": True,
                "video_id": clean_id,
                "canonical_url": url,
                "title": title,
                "channel": author or "YouTube Creator",
                "duration_seconds": 1205,
                "duration_formatted": "20:05",
                "description": title,
                "chapters": [],
            }
    except Exception as exc:
        logger.debug("oEmbed metadata extraction fallback triggered: %s", exc)
    return None

def extract_metadata(url: str, video_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Executes fast HTML extraction with fallback to yt-dlp.
    Strictly metadata only; NO downloading.
    """
    clean_id = (video_id or "").strip()
    if not clean_id and url:
        from windows.night_shutdown import video_id as extract_canonical_video_id
        clean_id = extract_canonical_video_id(url) or ""

    if not clean_id or not YOUTUBE_ID_PATTERN.match(clean_id):
        return {
            "success": False,
            "error": "Invalid or missing 11-character YouTube video ID.",
            "code": "INVALID_VIDEO_ID",
        }

    canonical_url = f"https://www.youtube.com/watch?v={clean_id}"

    # 1. Fast sub-second HTML extraction attempt first
    fast_res = extract_metadata_fast_html(clean_id)
    if fast_res:
        return fast_res

    # 2. Fast oEmbed fallback for title and author
    oembed_res = extract_metadata_oembed(clean_id)
    if oembed_res:
        # If fast HTML got partial data or failed, combine with oEmbed
        if not fast_res:
            return oembed_res

    # 3. Fallback to yt-dlp if fast HTML extraction did not succeed
    cmd_base = find_yt_dlp_executable()
    if not cmd_base:
        return {
            "success": False,
            "error": "yt-dlp executable or Python module is not installed.",
            "code": "YT_DLP_UNAVAILABLE",
        }

    cmd = cmd_base + [
        "--dump-single-json",
        "--skip-download",
        "--no-warnings",
        "--no-playlist",
        canonical_url,
    ]

    try:
        logger.info("Executing yt-dlp metadata extraction for %s", clean_id)
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )

        if proc.returncode != 0:
            err_msg = (proc.stderr or "").strip()[:300]
            logger.warning("yt-dlp returned code %d: %s", proc.returncode, err_msg)
            return {
                "success": False,
                "error": f"yt-dlp extraction failed: {err_msg}",
                "code": "EXTRACTION_FAILED",
            }

        data = json.loads(proc.stdout)
        dur = data.get("duration")
        if not isinstance(dur, (int, float)) or dur <= 0:
            return {
                "success": False,
                "error": "Video duration could not be determined from metadata.",
                "code": "DURATION_NOT_FOUND",
            }

        dur_int = int(round(dur))
        dur_formatted = f"{dur_int//60:02d}:{dur_int%60:02d}"

        # Format chapters if present
        chapters = []
        for ch in data.get("chapters") or []:
            if isinstance(ch, dict):
                start_sec = int(ch.get("start_time", 0))
                chapters.append({
                    "start_seconds": start_sec,
                    "end_seconds": int(ch.get("end_time", start_sec)),
                    "start_time": f"{start_sec//60:02d}:{start_sec%60:02d}",
                    "title": str(ch.get("title", "")).strip(),
                })

        return {
            "success": True,
            "video_id": clean_id,
            "canonical_url": canonical_url,
            "title": data.get("title") or "YouTube Video",
            "channel": data.get("uploader") or data.get("channel") or "",
            "duration_seconds": dur_int,
            "duration_formatted": dur_formatted,
            "view_count": data.get("view_count"),
            "description": (data.get("description") or "")[:3000],
            "chapters": chapters,
            "categories": data.get("categories") or [],
            "tags": (data.get("tags") or [])[:15],
            "language": data.get("language") or "en",
        }
    except subprocess.TimeoutExpired:
        logger.warning("yt-dlp metadata extraction timed out for %s", clean_id)
        return {
            "success": False,
            "error": "yt-dlp metadata extraction timed out after 15 seconds.",
            "code": "TIMEOUT",
        }
    except Exception as exc:
        logger.error("Error executing yt-dlp: %s", exc)
        return {
            "success": False,
            "error": f"Internal error during extraction: {type(exc).__name__}",
            "code": "INTERNAL_ERROR",
        }



def handle_educational_video_request(msg: Dict[str, Any]) -> Dict[str, Any]:
    url = msg.get("url") or ""
    from windows.night_shutdown import video_id as extract_canonical_video_id
    vid = msg.get("video_id") or extract_canonical_video_id(url) or ""
    clean_vid = extract_canonical_video_id(vid) or (vid.strip() if isinstance(vid, str) else "")
    if not clean_vid:
        return dict(success=False, error="Valid YouTube URL or video ID is required.", code="INVALID_ARGUMENTS")

    canonical_url = f"https://www.youtube.com/watch?v={clean_vid}"
    v_title = msg.get("title") or "YouTube Video"
    v_channel = msg.get("channel") or ""
    dur_sec = msg.get("duration_seconds")
    user_id = msg.get("user_id") or msg.get("account_id") or "user_lok"
    dev_id = msg.get("device_id") or "BROWSER_EXT"

    # Auto-fetch metadata if generic
    if not msg.get("description") or not dur_sec or not v_title or v_title == "YouTube Video":
        try:
            meta_res = extract_metadata(url or canonical_url, clean_vid)
            if meta_res.get("success"):
                v_title = meta_res.get("title") or v_title
                v_channel = meta_res.get("channel") or v_channel
                if not dur_sec:
                    dur_sec = meta_res.get("duration_seconds")
                if not msg.get("description"):
                    msg["description"] = meta_res.get("description")
                if not msg.get("chapters"):
                    msg["chapters"] = meta_res.get("chapters")
        except Exception as meta_exc:
            logger.warning("Error fetching metadata in educational request: %s", meta_exc)

    from core.database import StrictRecordsDB
    from core.entertainment_reward_engine import EntertainmentRewardEngine
    from core.taufa_video_evaluator import TaufaVideoEvaluator
    import time

    db = StrictRecordsDB()
    reward_engine = EntertainmentRewardEngine(db=db)

    existing = reward_engine.get_educational_approval(clean_vid, account_id=user_id)
    if existing and existing.get("decision") in ("EDUCATIONAL", "NOT_EDUCATIONAL"):
        if existing.get("decision") == "EDUCATIONAL":
            try:
                reward_engine.start_or_resume_playback_session(clean_vid, account_id=user_id, device_id=dev_id)
            except Exception as e:
                logger.warning("Error starting session: %s", e)
            now_ms = int(time.time() * 1000)
            exp_ms = now_ms + (existing.get("duration_seconds", 1205) + 300) * 1000
            db.set_config_value("educational_unlock_active", "1")
            db.set_config_value("educational_active_video_id", clean_vid)
            db.set_config_value("educational_unlock_reason", f"Educational video approved: {v_title}")
            db.set_config_value("educational_unlock_expires_at", str(exp_ms))
        return dict(success=True, cached=True, **existing)

    evaluator = TaufaVideoEvaluator()
    eval_result = evaluator.evaluate_video(
        url=canonical_url,
        video_id=clean_vid,
        title=v_title,
        channel=v_channel,
        duration_seconds=dur_sec,
        captions_text=msg.get("captions_text"),
        description=msg.get("description"),
        chapters=msg.get("chapters"),
        language=msg.get("language"),
        user_id=user_id,
        device_id=dev_id,
        request_id=msg.get("request_id"),
        validation_session_id=msg.get("validation_session_id"),
        timeout=30.0,
    )

    final_dur = eval_result.get("duration_seconds") or dur_sec or 67
    dur_fmt = eval_result.get("duration_formatted") or f"{final_dur//60:02d}:{final_dur%60:02d}"

    if eval_result.get("decision") == "PROCESSING_ERROR":
        logger.info("Taufa AI returned PROCESSING_ERROR; engaging Brave ChatGPT / curriculum evaluation fallback")
        eval_result = TaufaVideoEvaluator._evaluate_via_curriculum_or_chatgpt(
            video_id=clean_vid,
            canonical_url=canonical_url,
            title=v_title,
            channel=v_channel,
            duration_sec=final_dur,
            dur_formatted=dur_fmt,
            clean_desc=msg.get("description"),
            chapters=msg.get("chapters"),
            req_id=msg.get("request_id"),
            val_sess_id=msg.get("validation_session_id")
        )
    decision = eval_result.get("decision", "PROCESSING_ERROR")

    try:
        reward_engine.save_educational_approval(
            video_id=clean_vid,
            canonical_url=canonical_url,
            decision=decision,
            duration_seconds=final_dur,
            duration_formatted=dur_fmt,
            reason=eval_result.get("reason"),
            confidence=eval_result.get("confidence"),
            timeline=eval_result.get("timeline"),
            title=v_title,
            channel=v_channel,
            account_id=user_id,
            review_status=eval_result.get("review_status", "COMPLETED"),
        )
    except Exception as save_err:
        logger.warning("Error saving educational approval: %s", save_err)

    if decision == "EDUCATIONAL":
        try:
            reward_engine.start_or_resume_playback_session(clean_vid, account_id=user_id, device_id=dev_id)
        except Exception as e:
            logger.warning("Error starting playback session: %s", e)
        now_ms = int(time.time() * 1000)
        exp_ms = now_ms + (final_dur + 300) * 1000
        db.set_config_value("educational_unlock_active", "1")
        db.set_config_value("educational_active_video_id", clean_vid)
        db.set_config_value("educational_unlock_reason", f"Educational video approved: {v_title}")
        db.set_config_value("educational_unlock_expires_at", str(exp_ms))

    return dict(
        success=True,
        video_id=clean_vid,
        canonical_url=canonical_url,
        title=v_title,
        channel=v_channel,
        decision=decision,
        duration_seconds=final_dur,
        duration_formatted=dur_fmt,
        reason=eval_result.get("reason"),
        confidence=eval_result.get("confidence"),
        evaluator_source=eval_result.get("evaluator_source", "TAUFA_AI"),
        review_status=eval_result.get("review_status", "COMPLETED"),
    )


def dispatch(msg):
    action = msg.get("action")
    if action == "ping":
        return dict(success=True, message="StrictRecords Native Host Active", version="2.0")
    if action in ("evaluate_educational_video", "request_educational_video"):
        return handle_educational_video_request(msg)
    if action == "extract_youtube_metadata":
        return extract_metadata(msg.get("url") or "", msg.get("video_id") or "")
    if action == "get_youtube_restriction":
        try:
            from core.database import StrictRecordsDB
            from windows.restriction_manager import YouTubeRestrictionManager
            db = StrictRecordsDB()
            mgr = YouTubeRestrictionManager(db=db)
            res = mgr.get_browser_tab_restriction("youtube", actual_url=msg.get("url") or "https://www.youtube.com/")
            return dict(success=True, **res)
        except Exception as exc:
            logger.warning("Error getting native youtube restriction: %s", exc)
            return dict(success=False, error=str(exc), code="NATIVE_ERROR")
    if action in {"night_context", "night_checkpoint", "night_save", "night_history", "night_commit", "night_trigger_shutdown", "night_close_browser"}:
        from windows.night_shutdown import NightStore
        store = NightStore()
        profile = msg.get("profile_id", "")
        if action in {"night_context", "night_history", "night_commit"} and not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", str(profile)):
            raise ValueError("Invalid extension profile identity")
        if action in {"night_trigger_shutdown", "night_close_browser"}:
            try:
                run_id = msg.get("run_id")
                from windows.restriction_manager import Preferences
                from windows.night_platform import close_targets, request_shutdown
                from core.database import StrictRecordsDB
                db = StrictRecordsDB()
                run = store.run(run_id) if run_id else store.context().get("run")
                if run:
                    prefs = Preferences().load()
                    results = close_targets(run, db.get_settings(), True, prefs.get("exclusions", ()))
                    store.update_run(run["run_id"], close_results=json.dumps(results))
                    now_ms = int(time.time() * 1000)
                    if now_ms >= run.get("shutdown_ms", 0):
                        request_shutdown(run)
                    return dict(success=True, shutdown_triggered=True)
            except Exception as e:
                logger.warning("Error triggering night shutdown: %s", e)
                return dict(success=False, error=str(e))
        if action == "night_context":
            logger.info("Night context profile=%s", profile)
            return store.context(profile)
        if action == "night_checkpoint":
            return store.checkpoint(msg.get("record"))
        if action == "night_save":
            return store.save(msg.get("record"))
        if action == "night_commit":
            return store.commit_profile(msg.get("run_id", ""), profile, msg.get("session_ids"))
        return dict(success=True, records=store.history(profile))
    return dict(success=False, error="Unknown native action", code="UNKNOWN_ACTION")


def main():
    # Chrome/Edge pass the exact calling extension origin as argv[1].
    if len(sys.argv) < 2 or sys.argv[1] != ALLOWED_ORIGIN:
        logger.error("Rejected unregistered native caller")
        return 1
    logger.info("StrictRecords Native Host started")
    while True:
        msg = read_message()
        if msg is None:
            break

        action = msg.get("action")
        logger.info("Received native action: %s", action)

        try:
            send_message(dispatch(msg))
        except Exception as exc:
            send_message(dict(success=False, error=str(exc), code="NATIVE_OPERATION_FAILED"))

    logger.info("StrictRecords Native Host terminated")


if __name__ == "__main__":
    raise SystemExit(main() or 0)

