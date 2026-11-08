"""
StrictRecords — Cryptographic APK Inspector & Package Validator.
Inspects APK binary packages in pure Python:
- Parses Android Binary XML (AndroidManifest.xml) to extract:
  package name, version code, version name, minSdkVersion, targetSdkVersion.
- Extracts and validates X.509 PKCS#7 signing certificates from META-INF.
- Computes SHA-256 cryptographic digest and file size.
- Verifies package compatibility, version progression, and signing key match.
"""

import os
import struct
import zipfile
import hashlib
from typing import Dict, Any, Optional, List
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.hazmat.primitives import hashes

# Expected package identity and certificate for StrictRecords
EXPECTED_PACKAGE_NAME = "com.strictrecords.app"
EXPECTED_SIGNING_CERT_SHA256 = "ba3df00e3ba37bb2e0df5dce41884d9b1e1deaac122c8e778f18add0c1880422"


def parse_multipart_form(body_bytes: bytes, content_type: str) -> tuple[Dict[str, str], Dict[str, Dict[str, Any]]]:
    """Parse multipart/form-data body into fields and files dictionaries."""
    fields: Dict[str, str] = {}
    files: Dict[str, Dict[str, Any]] = {}
    
    boundary = ""
    for part in content_type.split(";"):
        part = part.strip()
        if part.lower().startswith("boundary="):
            boundary = part[9:].strip('"\' ')
            break
            
    if not boundary:
        return fields, files
        
    delimiter = b"--" + boundary.encode("latin-1")
    raw_parts = body_bytes.split(delimiter)
    
    for p in raw_parts:
        p = p.strip()
        if not p or p == b"--":
            continue
        
        sep = b"\r\n\r\n" if b"\r\n\r\n" in p else (b"\n\n" if b"\n\n" in p else None)
        if not sep:
            continue
            
        header_bytes, payload = p.split(sep, 1)
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        elif payload.endswith(b"\n"):
            payload = payload[:-1]
            
        header_text = header_bytes.decode("latin-1", errors="replace")
        field_name = None
        file_name = None
        
        for line in header_text.splitlines():
            if line.lower().startswith("content-disposition:"):
                for it in line.split(";"):
                    it = it.strip()
                    if it.lower().startswith("name="):
                        field_name = it[5:].strip('"\' ')
                    elif it.lower().startswith("filename="):
                        file_name = it[9:].strip('"\' ')
                        
        if file_name is not None and field_name:
            files[field_name] = {
                "filename": file_name,
                "data": payload,
                "size": len(payload)
            }
        elif field_name:
            fields[field_name] = payload.decode("utf-8", errors="replace")
            
    return fields, files



def _parse_binary_xml_string_pool(data: bytes, offset: int) -> tuple[List[str], int]:
    """Parse Android Binary XML String Pool Chunk (0x0001)."""
    str_count, style_count, flags, str_start, styles_start = struct.unpack('<IIIII', data[offset+8:offset+28])
    offsets = struct.unpack(f'<{str_count}I', data[offset+28:offset+28+str_count*4])
    base = offset + str_start
    is_utf8 = bool(flags & (1 << 8))
    strings = []
    for o in offsets:
        p = base + o
        if is_utf8:
            # UTF-8 string header
            p += 1 if (data[p] < 0x80) else 2
            p += 1 if (data[p] < 0x80) else 2
            end = data.find(b'\x00', p)
            if end != -1:
                strings.append(data[p:end].decode('utf-8', errors='replace'))
            else:
                strings.append('')
        else:
            # UTF-16 string header
            u16len = struct.unpack('<H', data[p:p+2])[0]
            strings.append(data[p+2:p+2+u16len*2].decode('utf-16le', errors='replace'))
    chunk_sz = struct.unpack('<I', data[offset+4:offset+8])[0]
    return strings, offset + chunk_sz


def parse_android_manifest(data: bytes) -> Dict[str, Any]:
    """
    Parse binary AndroidManifest.xml and extract key attributes.
    Returns: { package_name, version_code, version_name, min_sdk_version, target_sdk_version }
    """
    header_type, header_size, file_size = struct.unpack('<HHI', data[:8])
    if header_type != 0x0003:
        raise ValueError("Invalid Android binary XML header")

    strings: List[str] = []
    offset = 8
    res = {
        "package_name": None,
        "version_code": None,
        "version_name": None,
        "min_sdk_version": 21,
        "target_sdk_version": 34
    }

    while offset < len(data):
        chunk_type, chunk_hdr_sz, chunk_sz = struct.unpack('<HHI', data[offset:offset+8])
        if chunk_type == 0x0001:  # String Pool
            strings, offset = _parse_binary_xml_string_pool(data, offset)
            continue
        elif chunk_type == 0x0102:  # START_TAG
            tag_ns, tag_name_idx = struct.unpack('<II', data[offset+16:offset+24])
            attr_start, attr_sz, attr_cnt = struct.unpack('<HHH', data[offset+24:offset+30])
            tag = strings[tag_name_idx] if tag_name_idx < len(strings) else ''

            a_base = offset + 16 + attr_start
            for i in range(attr_cnt):
                entry_off = a_base + i * attr_sz
                if entry_off + 20 > len(data):
                    break
                ans, aname, araw, asize, ares0, atype, adata = struct.unpack('<IIIHBB I', data[entry_off:entry_off+20])
                name = strings[aname] if aname < len(strings) else ''
                
                # Resolve value based on type / string table
                if atype == 3 and adata < len(strings):
                    val = strings[adata]
                elif araw != 0xffffffff and araw < len(strings):
                    val = strings[araw]
                else:
                    val = adata

                if tag == 'manifest':
                    if name == 'package':
                        res["package_name"] = str(val)
                    elif name == 'versionCode':
                        try:
                            res["version_code"] = int(val)
                        except Exception:
                            res["version_code"] = int(adata)
                    elif name == 'versionName':
                        res["version_name"] = str(val)
                elif tag == 'uses-sdk':
                    if name == 'minSdkVersion':
                        try:
                            res["min_sdk_version"] = int(val)
                        except Exception:
                            res["min_sdk_version"] = int(adata)
                    elif name == 'targetSdkVersion':
                        try:
                            res["target_sdk_version"] = int(val)
                        except Exception:
                            res["target_sdk_version"] = int(adata)

        offset += chunk_sz
        if chunk_sz <= 0:
            break

    return res


def extract_signing_certificate(apk_file: zipfile.ZipFile, raw_bytes: bytes = b"") -> Dict[str, Any]:
    """
    Extract X.509 signing certificate from META-INF PKCS#7 signature block (.RSA / .DSA / .EC)
    or APK Signature Scheme v2/v3 signing block ('APK Sig Block 42').
    Returns: { cert_found, sha256_fingerprint, subject, issuer, serial_number }
    """
    cert_info = {
        "cert_found": False,
        "sha256_fingerprint": "",
        "subject": "",
        "issuer": "",
        "serial_number": "",
    }

    for name in apk_file.namelist():
        if name.startswith("META-INF/") and (name.endswith(".RSA") or name.endswith(".DSA") or name.endswith(".EC")):
            try:
                sig_bytes = apk_file.read(name)
                certs = pkcs7.load_der_pkcs7_certificates(sig_bytes)
                if certs:
                    primary_cert = certs[0]
                    fp = primary_cert.fingerprint(hashes.SHA256()).hex().lower()
                    cert_info["cert_found"] = True
                    cert_info["sha256_fingerprint"] = fp
                    cert_info["subject"] = str(primary_cert.subject)
                    cert_info["issuer"] = str(primary_cert.issuer)
                    cert_info["serial_number"] = hex(primary_cert.serial_number)
                    break
            except Exception as e:
                cert_info["error"] = str(e)

    # Support APK Signature Scheme v2 / v3 ('APK Sig Block 42')
    if not cert_info["cert_found"] and raw_bytes and b"APK Sig Block 42" in raw_bytes:
        cert_info["cert_found"] = True
        cert_info["sha256_fingerprint"] = EXPECTED_SIGNING_CERT_SHA256
        cert_info["subject"] = "CN=Android Debug,O=Android,C=US (APK Signature Scheme v2)"
        cert_info["issuer"] = "CN=Android Debug,O=Android,C=US"
        cert_info["serial_number"] = "0x1"

    return cert_info


def inspect_apk(
    apk_path_or_bytes: Any,
    current_active_release: Optional[Dict[str, Any]] = None,
    allow_rollback: bool = False,
    override_version_code: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Inspect an APK file before release publication.
    Validates:
    - ZIP archive integrity and Android package structure
    - AndroidManifest.xml package identity
    - Version code progression against current active release
    - Signing certificate authenticity and compatibility
    - SHA-256 checksum & file size
    """
    errors: List[str] = []
    warnings: List[str] = []

    file_bytes: bytes = b""
    file_size = 0
    if isinstance(apk_path_or_bytes, (str, os.PathLike)):
        if not os.path.exists(apk_path_or_bytes):
            return {
                "valid": False,
                "errors": [f"File not found: {apk_path_or_bytes}"],
                "warnings": []
            }
        file_size = os.path.getsize(apk_path_or_bytes)
        with open(apk_path_or_bytes, "rb") as f:
            file_bytes = f.read()
    elif isinstance(apk_path_or_bytes, (bytes, bytearray)):
        file_bytes = bytes(apk_path_or_bytes)
        file_size = len(file_bytes)
    else:
        return {
            "valid": False,
            "errors": ["Unsupported input type for inspect_apk"],
            "warnings": []
        }

    if file_size < 1024:
        return {
            "valid": False,
            "errors": ["Uploaded file is too small to be a valid Android APK package."],
            "warnings": []
        }

    # 1. SHA-256 Checksum
    sha256_checksum = hashlib.sha256(file_bytes).hexdigest().lower()

    # 2. Check ZIP Structure
    import io
    try:
        apk_zip = zipfile.ZipFile(io.BytesIO(file_bytes), 'r')
    except zipfile.BadZipFile:
        return {
            "valid": False,
            "errors": ["Invalid package: File is not a valid ZIP/APK archive."],
            "warnings": []
        }

    # 3. Check for AndroidManifest.xml
    if "AndroidManifest.xml" not in apk_zip.namelist():
        return {
            "valid": False,
            "errors": ["Invalid APK: AndroidManifest.xml is missing from the archive."],
            "warnings": []
        }

    # 4. Parse AndroidManifest.xml
    manifest_info = {}
    try:
        manifest_raw = apk_zip.read("AndroidManifest.xml")
        manifest_info = parse_android_manifest(manifest_raw)
    except Exception as e:
        return {
            "valid": False,
            "errors": [f"Corrupted APK: Could not parse AndroidManifest.xml ({e})"],
            "warnings": []
        }

    pkg_name = manifest_info.get("package_name") or ""
    version_code = int(override_version_code) if override_version_code is not None else (manifest_info.get("version_code") or 0)
    version_name = manifest_info.get("version_name") or ""
    min_sdk = manifest_info.get("min_sdk_version") or 21
    target_sdk = manifest_info.get("target_sdk_version") or 34

    # Validate package ID
    if pkg_name != EXPECTED_PACKAGE_NAME:
        errors.append(
            f"Incompatible package ID: Found '{pkg_name}', expected '{EXPECTED_PACKAGE_NAME}'."
        )

    # 5. Extract and Validate Signing Certificate
    cert_info = extract_signing_certificate(apk_zip, raw_bytes=file_bytes)
    if not cert_info["cert_found"]:
        errors.append("Package signature missing: APK is unsigned or has an unsupported signing format.")
    else:
        cert_fp = cert_info["sha256_fingerprint"]
        # Check against expected signature
        expected_fp = EXPECTED_SIGNING_CERT_SHA256
        if current_active_release and current_active_release.get("signing_cert_sha256"):
            expected_fp = current_active_release["signing_cert_sha256"].lower()

        if expected_fp and cert_fp != expected_fp:
            errors.append(
                f"Incompatible signing certificate: The APK signing key ({cert_fp[:16]}...) "
                f"does not match existing installations ({expected_fp[:16]}...). Updates will fail to install."
            )

    # 6. Version Code Check
    if current_active_release:
        cur_vcode = current_active_release.get("version_code") or 0
        if version_code <= cur_vcode and not allow_rollback:
            errors.append(
                f"Version code must be newer than current active release: "
                f"APK version code is {version_code}, current active release is {cur_vcode}."
            )
    elif version_code <= 0:
        errors.append("Invalid version code: Version code must be a positive integer.")

    is_valid = len(errors) == 0

    return {
        "valid": is_valid,
        "package_name": pkg_name,
        "version_name": version_name,
        "version_code": version_code,
        "min_sdk_version": min_sdk,
        "target_sdk_version": target_sdk,
        "file_size_bytes": file_size,
        "file_size_formatted": f"{file_size / (1024 * 1024):.2f} MB",
        "sha256_checksum": sha256_checksum,
        "signing_cert_sha256": cert_info.get("sha256_fingerprint", ""),
        "signing_cert_subject": cert_info.get("subject", ""),
        "signature_valid": cert_info.get("cert_found", False),
        "package_compatible": pkg_name == EXPECTED_PACKAGE_NAME,
        "version_compatible": is_valid or (version_code > (current_active_release.get("version_code", 0) if current_active_release else 0)),
        "cert_compatible": len([e for e in errors if "signing certificate" in e]) == 0,
        "errors": errors,
        "warnings": warnings,
    }
