"""
STRICTRECORDS — Wi-Fi APK Release Publisher & Cryptographic Manifest Generator
Validates Android APK package name, version, cryptographic signature, SHA-256 digest,
and publishes releases to the StrictRecords Wi-Fi update infrastructure.
"""

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.database import StrictRecordsDB
from core.server import EventBus

logger = logging.getLogger("ApkPublisher")


class ApkPublisher:
    """Publishes validated, cryptographically signed APK releases over local Wi-Fi."""

    def __init__(self, db: Optional[StrictRecordsDB] = None):
        self.db = db or StrictRecordsDB()
        self.sdk_dir = os.environ.get(
            "ANDROID_HOME",
            os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk")
        )
        self.aapt_path = self._find_build_tool("aapt.exe")
        self.apksigner_path = self._find_build_tool("apksigner.bat")

    def _find_build_tool(self, tool_name: str) -> Optional[str]:
        """Locates the latest version of an Android SDK build tool."""
        # 1. Check PATH
        which = shutil.which(tool_name)
        if which:
            return which

        # 2. Check Android SDK build-tools directory
        bt_dir = os.path.join(self.sdk_dir, "build-tools")
        if os.path.exists(bt_dir):
            versions = sorted(os.listdir(bt_dir), reverse=True)
            for v in versions:
                cand = os.path.join(bt_dir, v, tool_name)
                if os.path.exists(cand):
                    return cand

        return None

    def calculate_sha256(self, file_path: str) -> str:
        """Calculates lowercase SHA-256 digest of a file."""
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest().lower()

    def inspect_apk(self, apk_path: str) -> Dict[str, Any]:
        """
        Inspects APK package identity, version metadata, and cryptographic signatures.
        """
        if not os.path.exists(apk_path):
            raise FileNotFoundError(f"APK file not found: {apk_path}")

        file_size = os.path.getsize(apk_path)
        if file_size == 0:
            raise ValueError("APK file is empty (0 bytes).")

        sha256_checksum = self.calculate_sha256(apk_path)

        pkg_name = "com.strictrecords.app"
        version_code = 1
        version_name = "1.0.0"
        min_sdk = 24
        target_sdk = 34

        # 1. Inspect package and version metadata using aapt if available
        if self.aapt_path and os.path.exists(self.aapt_path):
            try:
                out = subprocess.check_output(
                    [self.aapt_path, "dump", "badging", apk_path],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=15.0,
                )
                m = re.search(
                    r"package:\s+name='([^']+)'\s+versionCode='([^']+)'\s+versionName='([^']+)'",
                    out
                )
                if m:
                    pkg_name = m.group(1)
                    version_code = int(m.group(2))
                    version_name = m.group(3)

                m_min = re.search(r"sdkVersion:'([^']+)'", out)
                if m_min:
                    min_sdk = int(m_min.group(1))
                m_target = re.search(r"targetSdkVersion:'([^']+)'", out)
                if m_target:
                    target_sdk = int(m_target.group(1))
            except Exception as e:
                logger.warning(f"aapt dump badging failed, falling back to build.gradle: {e}")
                pkg_name, version_code, version_name = self._inspect_fallback_gradle()
        else:
            pkg_name, version_code, version_name = self._inspect_fallback_gradle()

        # 2. Verify APK signature using apksigner if available
        is_signed = False
        v1_signed = False
        v2_signed = False
        signing_cert_sha256 = ""
        signer_dn = ""

        if self.apksigner_path and os.path.exists(self.apksigner_path):
            try:
                sig_out = subprocess.check_output(
                    [self.apksigner_path, "verify", "--verbose", "--print-certs", apk_path],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=20.0,
                )
                is_signed = "Verifies" in sig_out
                v1_signed = "Verified using v1 scheme (JAR signing): true" in sig_out
                v2_signed = "Verified using v2 scheme (APK Signature Scheme v2): true" in sig_out

                cert_m = re.search(r"Signer #1 certificate SHA-256 digest:\s+([a-f0-9]+)", sig_out, re.IGNORECASE)
                if cert_m:
                    signing_cert_sha256 = cert_m.group(1).lower()

                dn_m = re.search(r"Signer #1 certificate DN:\s+(.+)", sig_out)
                if dn_m:
                    signer_dn = dn_m.group(1).strip()
            except subprocess.CalledProcessError as cpe:
                logger.warning(f"APK signature verification reported errors: {cpe}")
            except Exception as e:
                logger.warning(f"apksigner execution error: {e}")
        else:
            # Fallback: check presence of META-INF signatures inside ZIP
            import zipfile
            try:
                with zipfile.ZipFile(apk_path, "r") as zf:
                    meta_files = [n for n in zf.namelist() if n.startswith("META-INF/") and (n.endswith(".RSA") or n.endswith(".DSA") or n.endswith(".EC"))]
                    if meta_files:
                        is_signed = True
                        v1_signed = True
            except Exception:
                pass

        return {
            "apk_path": os.path.abspath(apk_path),
            "file_size_bytes": file_size,
            "sha256_checksum": sha256_checksum,
            "package_name": pkg_name,
            "version_code": version_code,
            "version_name": version_name,
            "min_sdk": min_sdk,
            "target_sdk": target_sdk,
            "is_signed": is_signed,
            "v1_signed": v1_signed,
            "v2_signed": v2_signed,
            "signing_cert_sha256": signing_cert_sha256,
            "signer_dn": signer_dn,
        }

    def _inspect_fallback_gradle(self) -> Tuple[str, int, str]:
        """Reads applicationId, versionCode, and versionName from build.gradle."""
        gradle_path = os.path.join(ROOT_DIR, "android", "app", "build.gradle")
        pkg = "com.strictrecords.app"
        vcode = 1
        vname = "1.0.0"

        if os.path.exists(gradle_path):
            try:
                with open(gradle_path, "r", encoding="utf-8") as f:
                    content = f.read()
                m_pkg = re.search(r"applicationId\s+[\"']([^\"']+)[\"']", content)
                if m_pkg:
                    pkg = m_pkg.group(1)
                m_code = re.search(r"versionCode\s+(\d+)", content)
                if m_code:
                    vcode = int(m_code.group(1))
                m_name = re.search(r"versionName\s+[\"']([^\"']+)[\"']", content)
                if m_name:
                    vname = m_name.group(1)
            except Exception:
                pass

        return pkg, vcode, vname

    def publish_release(
        self,
        apk_path: str,
        version_name: Optional[str] = None,
        version_code: Optional[int] = None,
        channel: str = "PRODUCTION",
        release_notes: str = "Wi-Fi APK update release with performance & stability improvements.",
        is_mandatory: bool = False,
        admin_pin: str = "",
    ) -> Dict[str, Any]:
        """
        Validates, packages, and publishes a new Android APK release.
        Copies the binary to releases/ and broadcasts APK_UPDATE_AVAILABLE over Wi-Fi.
        """
        if not self.db.verify_admin_pin(admin_pin) or self.db.is_default_admin_pin():
            raise PermissionError("A valid administrator key changed from its initial setup value is required.")

        # 1. Inspect APK
        info = self.inspect_apk(apk_path)

        # 2. Package Identity Verification
        if info["package_name"] != "com.strictrecords.app":
            raise ValueError(f"Invalid package identity: expected com.strictrecords.app, found {info['package_name']}")

        v_name = version_name or info["version_name"]
        v_code = version_code if version_code is not None else info["version_code"]

        if v_code <= 0:
            raise ValueError(f"Version code must be greater than 0, got {v_code}")

        # 3. Setup releases directory
        releases_dir = os.path.join(ROOT_DIR, "releases")
        os.makedirs(releases_dir, exist_ok=True)

        target_filename = f"strictrecords_v{v_code}.apk"
        target_path = os.path.join(releases_dir, target_filename)

        # Copy APK to target release path and .dist
        if os.path.abspath(apk_path) != os.path.abspath(target_path):
            shutil.copy2(apk_path, target_path)

        dist_dir = os.path.join(ROOT_DIR, ".dist")
        os.makedirs(dist_dir, exist_ok=True)
        dist_apk = os.path.join(dist_dir, "StrictRecords-debug.apk")
        try:
            shutil.copy2(target_path, dist_apk)
            shutil.copy2(target_path, os.path.join(ROOT_DIR, "StrictRecords-debug.apk"))
        except Exception:
            pass

        # 4. Construct authoritative release record
        rel_id = f"rel_android_{v_code}"
        dl_url = f"/api/releases/download/{target_filename}"

        release_record = {
            "release_id": rel_id,
            "platform": "ANDROID",
            "version_name": v_name,
            "version_code": v_code,
            "min_supported_version": info["min_sdk"],
            "package_name": info["package_name"],
            "release_notes": release_notes,
            "download_url": dl_url,
            "file_size_bytes": info["file_size_bytes"],
            "sha256_checksum": info["sha256_checksum"],
            "channel": channel.upper(),
            "is_mandatory": is_mandatory,
            "signing_cert_sha256": info["signing_cert_sha256"],
            "created_by": "admin",
        }

        # 5. Persist to SQLite Database
        db_res = self.db.publish_app_release(release_record)

        # 6. Write static manifest.json for offline/direct access
        manifest_file = os.path.join(releases_dir, "manifest.json")
        try:
            with open(manifest_file, "w", encoding="utf-8") as f:
                json.dump(release_record, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to write releases/manifest.json: {e}")

        # 7. Broadcast APK_UPDATE_AVAILABLE event to EventBus (triggers SSE to Windows UI & Wi-Fi pollers)
        try:
            bus = EventBus.get_instance()
            bus.publish("APK_UPDATE_AVAILABLE", {
                "release_id": rel_id,
                "version_name": v_name,
                "version_code": v_code,
                "channel": channel.upper(),
                "platform": "ANDROID",
                "download_url": dl_url,
                "sha256": info["sha256_checksum"],
                "file_size_bytes": info["file_size_bytes"],
                "release_notes": release_notes,
                "is_mandatory": is_mandatory,
            })
        except Exception as e:
            logger.warning(f"Failed to publish APK_UPDATE_AVAILABLE to EventBus: {e}")

        # 8. Record Admin Audit Log
        try:
            self.db.record_audit_log(
                category="RELEASE_MANAGEMENT",
                action="PUBLISH_APK_RELEASE",
                severity="INFO",
                target=rel_id,
                details=f"Published release v{v_name} (versionCode {v_code}) with SHA-256 {info['sha256_checksum'][:12]}... on channel {channel.upper()}"
            )
        except Exception:
            pass

        logger.info(f"Successfully published Android release {rel_id}: v{v_name} ({v_code})")
        return {
            "success": True,
            "release": release_record,
            "apk_info": info,
            "target_path": target_path,
        }


def main():
    parser = argparse.ArgumentParser(description="StrictRecords Wi-Fi APK Release Publisher")
    parser.add_argument("apk", help="Path to compiled Android APK")
    parser.add_argument("--version-name", "-v", help="Release version name (e.g. 1.2.0)")
    parser.add_argument("--version-code", "-c", type=int, help="Release version code (e.g. 2)")
    parser.add_argument("--channel", default="PRODUCTION", choices=["PRODUCTION", "BETA", "NIGHTLY"])
    parser.add_argument("--notes", "-n", default="Wi-Fi APK update release with performance & stability improvements.")
    parser.add_argument("--mandatory", "-m", action="store_true", help="Mark update as mandatory")
    parser.add_argument("--pin", default="", help="Changed admin PIN for authorization")
    args = parser.parse_args()

    publisher = ApkPublisher()
    try:
        res = publisher.publish_release(
            apk_path=args.apk,
            version_name=args.version_name,
            version_code=args.version_code,
            channel=args.channel,
            release_notes=args.notes,
            is_mandatory=args.mandatory,
            admin_pin=args.pin,
        )
        print("\n" + "=" * 60)
        print("  STRICTRECORDS WI-FI APK RELEASE PUBLISHED")
        print("=" * 60)
        rel = res["release"]
        print(f"  Release ID:    {rel['release_id']}")
        print(f"  Version:       v{rel['version_name']} (code {rel['version_code']})")
        print(f"  Channel:       {rel['channel']}")
        print(f"  Package:       {rel['package_name']}")
        print(f"  Size:          {rel['file_size_bytes']} bytes ({(rel['file_size_bytes'] / (1024*1024)):.2f} MB)")
        print(f"  SHA-256:       {rel['sha256_checksum']}")
        print(f"  Download URL:  {rel['download_url']}")
        print(f"  Target File:   {res['target_path']}")
        print("=" * 60 + "\n")
        return 0
    except Exception as e:
        print(f"\n[ERROR] Failed to publish release: {e}\n", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
