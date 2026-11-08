# iPhone/iPad camera deployment for Strict Session

The registered iOS browser companion combines the Windows timer with explicit
video-only camera setup. Strict Session now requires fresh decoded iOS frames
before Session Ready/start/resume. Missing or stale video pauses study monitoring
and blocks successful completion. Normal dashboard and APK pairing/sync remain
independent. This is a browser companion, not a native iOS application.

## Required deployment inputs

1. A trusted public HTTPS/WSS origin for the isolated camera gateway. Production
   can use an owned hostname with a Named Tunnel or controlled TLS proxy. The
   existing Quick Tunnel mode supports temporary testing without a domain;
   addresses change after restart and must pass the real HTTPS/WSS probe.
2. Authenticated TURN for reliable cross-network WebRTC. The managed Cloudflare
   adapter issues expiring ICE credentials; coturn REST/HMAC is also supported.
   Keep long-lived provider/shared secrets server-side, never in a URL or chat.
   STUN/direct media can work on the same Wi-Fi but is not verified TURN relay.
3. Install `requirements-ios-camera.txt` into the Python environment used by the
   desktop runtime. Isolated tests use `.tmp/ios-camera-deps`; that is not a
   production dependency installation.
4. Optional native iOS integration requires the existing Xcode project, Team ID,
   bundle ID and associated-domain entitlement. None is present in this checkout.
   Do not advertise Universal Links until the signed app and AASA are deployed.

## Recommended HTTPS proxy mode

Use the supplied Caddy example after setting a real hostname, DNS and network
reachability. Caddy obtains and renews a public CA certificate automatically.
Do not use `tls internal`, localhost names, IP-based certificates or a certificate
warning bypass. ACME may require public ports 80/443 or a DNS challenge adapter.

Set these environment variables in the desktop runtime and Caddy service:

```
STRICTRECORDS_IOS_CAMERA_ORIGIN=https://<your-real-hostname>
STRICTRECORDS_IOS_CAMERA_HOSTNAME=<your-real-hostname>
STRICTRECORDS_IOS_CAMERA_PROXY=1
STRICTRECORDS_IOS_CAMERA_PORT=9443
STRICTRECORDS_IOS_CAMERA_PROXY_SECRET=<random-secret-at-least-32-characters>
STRICTRECORDS_IOS_CAMERA_TURN_URL=turns:<your-turn-host>:5349?transport=tcp
STRICTRECORDS_IOS_CAMERA_TURN_SECRET=<your-private-coturn-shared-secret>
```

Store secrets in service configuration protected by Windows account permissions,
not in this repository. The proxy-mode listener binds loopback and requires the
proxy secret plus exact Host and Origin checks. Never forward the general
backend. The gateway disables access logging and accepts signaling metadata only.

For direct TLS, omit PROXY=1 and set `STRICTRECORDS_IOS_CAMERA_CERT` to the full
chain PEM and `STRICTRECORDS_IOS_CAMERA_KEY` to its private key. Manage public CA
renewal externally and restart only the camera listener to reload renewed files.
Direct TLS serves on the configured port, which must match the public origin or
be explicitly forwarded. TLS 1.2 is the minimum.

The gateway probes the public origin with the platform CA verifier and hostname
verification. Redirects are rejected. It enables invitations only after the
verified origin returns this listener's instance challenge. A certificate-file
load alone never counts as trusted TLS verification.

## Registration and session flow

Join the timer companion on the same trusted origin first; browser storage is
origin-scoped, so a registration stored on `http://192.168...` is not reused across
origins. Generate a fresh timer QR from Windows once the HTTPS gateway is ready.
This retains the existing preparation/session identity and creates no score event.

Create the combined timer/camera QR from Windows Join Devices. Its 180-second
timer enrollment is consumed separately from the camera capability. On iOS,
**Start Camera** requests a registered, preparation-scoped camera grant and then
asks for video permission. No active study session is required to test capture.
Windows must receive fresh decoded frames before readiness/start is allowed.
Activation retains that preparation-scoped feed; ending/expiring the preparation
invalidates it. Camera credentials stay in page memory and rotate every ten
minutes. Ordinary network reconnects reuse the live grant; invitations remain
single-use. Starting camera setup does not start the Windows study timer.

WSS negotiates a video-only WebRTC peer with the authenticated Windows receiver.
SDP containing audio or data is rejected. No microphone is requested. DTLS-SRTP
media is decoded in memory and processed by a separate StrictGuard frame monitor.
The existing floating preview can select Windows webcam or iPhone camera; it never
substitutes an iPhone image for the Windows webcam. No continuous recording or raw
media upload endpoint exists.

The camera page keeps the same timer participant alive while visible. A short,
nonsecret browser lease prevents a hidden old timer tab overwriting that report.
Background/lock releases capture and reports interruption; returning requires an
explicit Resume Camera. Stop Camera releases media without deleting timer pairing.
Ending the Windows session revokes camera grants and closes peers.

The production Windows manager always requires iOS camera readiness; it does not
depend on an opt-in environment flag. The readiness gate requires receiver frame
count, fresh arrival and preview timestamps (under three seconds), a live stream
and the correct iPhone source. Permission flags or signaling alone cannot pass.
Missing/stalled video makes the existing monitoring check false and pauses study
accrual. It never awards or deducts points independently. The existing iPad timer
heartbeat requirement remains in force as an additional check.

## Deployment and physical-device validation still required

Run `python tools/diagnose_ios_camera.py` before pairing. Verify Safari's actual
secure-context diagnostics and camera denial/error name. Test physical iPhone
video reception, airplane mode, network switching, forced TURN relay, permission
revocation, screen lock/resume and end-session revocation. A local synthetic
WebRTC test cannot establish those results. No native application or Universal
Link validation can be claimed without the signed iOS project.

Primary references: [WebRTC connectivity](https://webrtc.org/getting-started/peer-connections),
[aiortc API](https://aiortc.readthedocs.io/en/latest/api.html),
[Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https),
[Apple camera background interruption](https://developer.apple.com/documentation/avfoundation/avcapturesession/interruptionreason/videodevicenotavailableinbackground).
