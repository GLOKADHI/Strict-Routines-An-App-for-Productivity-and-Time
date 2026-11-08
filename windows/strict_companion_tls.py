"""Optional HTTPS companion listener, using a user-configured trusted certificate.

Never installs a root certificate or bypasses browser certificate warnings.
"""
import logging
import os
import ssl
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit


def attach_companion_tls(handler):
    cert=os.environ.get('STRICTRECORDS_COMPANION_CERT');key=os.environ.get('STRICTRECORDS_COMPANION_KEY')
    origin=os.environ.get('STRICTRECORDS_COMPANION_ORIGIN','')
    if not cert or not key:return None
    try:
        url=urlsplit(origin)
        if url.scheme!='https' or not url.hostname:raise ValueError('HTTPS companion origin required')
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(cert,key)
        class CompanionHandler(handler):
            def do_GET(self):
                if self.path.split('?',1)[0]!='/strict-session/companion':
                    self._send_json({'success':False,'error':'Companion endpoint only'},status=404);return
                super().do_GET()
            def do_POST(self):
                if self.path not in {'/api/strict_session/preparation/join','/api/strict_session/preparation/heartbeat'}:
                    self._send_json({'success':False,'error':'Companion endpoint only'},status=404);return
                super().do_POST()
        server=ThreadingHTTPServer(('0.0.0.0',url.port or 443),CompanionHandler)
        server.socket=context.wrap_socket(server.socket,server_side=True)
        threading.Thread(target=server.serve_forever,daemon=True,name='StrictCompanionHTTPS').start()
        def stop():server.shutdown();server.server_close()
        return stop
    except Exception:
        logging.getLogger('StrictCompanionTLS').exception('HTTPS companion listener could not start; readiness remains unconfirmed')
        return None
