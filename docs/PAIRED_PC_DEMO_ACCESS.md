# Demo Unlock through the paired PC

Android uses the already registered PC when no cloud authority is configured.
It connects to the same host and the next TCP port (8765 -> 8766), retaining
the existing device credential. An explicitly configured cloud authority is
retained; connection failures never switch between authorities.

The companion service binds only to 127.0.0.1. Tailscale forwards port 8766
within the private tailnet, just as port 8765 reaches the existing PC service.
The HTTP connection is carried inside the encrypted private VPN. No Funnel,
public port forwarding, Google authentication, or cloud-points setup is needed.

The helper reads the existing PC device registry to authenticate each request.
It adds only the four-column demo_access_state table to the existing database.
The original demo receipt rules remain: effective access points 81 for at most
30 seconds, one activation per account on this PC per rolling seven days,
and no writes to points, proof, attendance, or history records. SQLite
transactions serialize concurrent devices and retain the cooldown through
phone reinstalls and PC/helper restarts. A different PC is a different local
authority; cross-PC cooldown migration is not implemented by this helper.

Start/register the helper from the workspace:

    python scripts/install_paired_demo_service.py --database data/strictrecords.db --register-startup
    tailscale serve --bg --tcp=8766 tcp://127.0.0.1:8766

The installer registers only HKCU Run / StrictRoutinesDemoAccess and launches
pythonw without a console window. It leaves the main EXE and its startup entry
unchanged. The helper and its source workspace must remain available. Windows
logon starts the helper; the Tailscale service maintains its private forwarding.
Removing that specific Run value disables helper autostart.

Health: GET http://127.0.0.1:8766/health (no credentials).
Demo endpoints: GET /v1/demo-access/state and POST /v1/demo-access/activate,
using Authorization: Bearer plus X-Device-Id from existing device pairing.
Real activation consumes the weekly allowance; tests use temporary accounts.
