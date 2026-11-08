"""Read-only TLS diagnostic, with no media permissions or secret output."""
import json
import os
import socket
import ssl
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def diagnose(env=None):
    env = os.environ if env is None else env
    camera = env.get('STRICTRECORDS_IOS_CAMERA_ORIGIN', '')
    timer = env.get('STRICTRECORDS_COMPANION_ORIGIN', '')
    result = dict(camera_origin_configured=bool(camera), timer_https_configured=timer.startswith('https://'),
        legacy_default='HTTP LAN timer; not a trusted camera secure context',
        native_ios_project_present=any(Path(name).is_dir() for name in ('ios', 'iOS')),
        certificate_verified=False, microphone_requested=False)
    if not camera:
        result['cause'] = 'NO_TRUSTED_CAMERA_ORIGIN_CONFIGURED'
        return result
    from core.strict_ios_camera import trusted_origin
    try:
        origin = trusted_origin(camera)
        uri = urlsplit(origin)
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        with socket.create_connection((uri.hostname, uri.port or 443), timeout=6) as connection:
            with context.wrap_socket(connection, server_hostname=uri.hostname) as tls:
                cert = tls.getpeercert()
                result.update(certificate_verified=True, hostname_verified=True, ca_chain_verified=True,
                    tls_version=tls.version(), not_after=cert.get('notAfter'),
                    subject_alternative_names=[value for kind, value in cert.get('subjectAltName', ()) if kind == 'DNS'],
                    cause='TLS_VERIFIED_DEVICE_CAMERA_CHECK_STILL_REQUIRED')
    except ssl.SSLCertVerificationError as error:
        result.update(cause='CERTIFICATE_VERIFICATION_FAILED', error_name=type(error).__name__,
                      certificate_error=error.verify_message, verification_code=error.verify_code)
    except (ValueError, OSError) as error:
        result.update(cause='ORIGIN_OR_NETWORK_FAILURE', error_name=type(error).__name__)
    return result


if __name__ == '__main__':
    print(json.dumps(diagnose(), indent=2))
