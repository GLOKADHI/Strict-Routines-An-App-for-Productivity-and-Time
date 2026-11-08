"""Windows presentation arbitration only; never decides access, proof or score state.

The restriction worker serializes native projections on its existing UI queue.
The scheduler remains the authority for occurrence availability and completion.
"""
import threading


def occurrence(row):
    return (str(row.get("activity_id", "")), int(row.get("schedule_version") or 1))


class DesktopOverlayCoordinator:
    def __init__(self, project):
        self._lock = threading.RLock()
        self._project = project
        self.robot_session = None
        self.robot_stage = "hidden"
        self.pending = None
        self.panel_identity = None
        self.panel_visible = False
        self.main_visible = True
        self.home_parked = False
        self.completed = False
        self._completed_identity = None
        self._last_projection = None

    def snapshot(self):
        with self._lock:
            active = self.robot_session is not None
            claimed = self.panel_identity is not None and self.main_visible
            minimized = active and (claimed or self.home_parked)
            return dict(ROBOT_RESTRICTION_ACTIVE=active,
                        ROBOT_VISIBLE=active and not minimized,
                        ROBOT_WALKING=active and self.robot_stage in {"entry", "walking", "slow_down", "stop"},
                        ROBOT_MINIMIZED=minimized,
                        HOURLY_WINDOW_PENDING=self.pending is not None,
                        HOURLY_ALERT_VISIBLE=self.pending is not None and not claimed and active,
                        HOURLY_PANEL_VISIBLE=self.panel_visible and self.main_visible,
                        HOURLY_SUBMISSION_COMPLETED=self.completed,
                        robot_session=self.robot_session,
                        hourly_identity=list(occurrence(self.pending)) if self.pending else None)

    def _publish(self, open_submission=None):
        state = self.snapshot()
        if state != self._last_projection or open_submission:
            self._last_projection = state
            self._project(state, dict(open_submission) if open_submission else None)
        return state

    def robot_started(self, session):
        with self._lock:
            if self.robot_session != session:
                self.robot_stage = "enter"
                self.home_parked = False
            self.robot_session = session
            return self._publish()

    def robot_stage_changed(self, session, stage):
        with self._lock:
            if session == self.robot_session:
                self.robot_stage = str(stage)
                self._publish()

    def robot_resolved(self, session=None):
        with self._lock:
            if session is not None and session != self.robot_session:
                return
            was_active = self.robot_session is not None
            self.robot_session = None
            self.robot_stage = "hidden"
            self.home_parked = False
            if was_active and self.pending and not self.panel_identity:
                self._open_pending()
            else:
                self._publish()

    def reconcile(self, row):
        """Called by the existing scheduler, including terminal/expired snapshots."""
        with self._lock:
            if row and occurrence(row) == self._completed_identity:
                row = None  # Ignore an in-flight scheduler snapshot after acceptance.
            old = occurrence(self.pending) if self.pending else None
            new = occurrence(row) if row else None
            self.pending = dict(row) if row else None
            if new != old:
                self.completed = False
                if self.panel_identity == old:
                    self.panel_identity = None
                    self.panel_visible = False
            return self._publish()

    def request_panel(self, identity, user_initiated=False):
        with self._lock:
            identity = tuple(identity)
            if not self.main_visible and not user_initiated:
                return False  # A hidden WebView cannot silently claim the desktop.
            if not user_initiated and (identity == self._completed_identity
                    or (self.pending and identity != occurrence(self.pending))):
                return False
            if self.robot_session is not None and not self.panel_identity and not user_initiated:
                return False
            self.panel_identity = identity
            self.main_visible = True
            self.home_parked = False
            self._publish()
            return True

    def _open_pending(self):
        if not self.pending:
            return False
        if self.panel_identity == occurrence(self.pending) and self.main_visible:
            return False
        self.panel_identity = occurrence(self.pending)
        self.main_visible = True
        self.home_parked = False
        self._publish(open_submission=self.pending)
        return True

    def open_pending(self):
        with self._lock:
            if self.panel_identity and self.panel_visible and self.main_visible:
                return False
            return self._open_pending()

    def can_open(self, row):
        with self._lock:
            return bool(self.pending and occurrence(row) == occurrence(self.pending)
                        and self.panel_identity == occurrence(row) and self.main_visible)

    def home_requested(self):
        with self._lock:
            self.home_parked = True
            self._publish()

    def home_finished(self):
        with self._lock:
            if not self._open_pending():
                self._publish()

    def panel_changed(self, identity, visible):
        with self._lock:
            identity = tuple(identity)
            if visible:
                # A stale DOM callback cannot claim a newer occurrence.
                if self.panel_identity != identity:
                    return
                self.panel_visible = True
            elif self.panel_identity == identity:
                self.panel_visible = False
                self.panel_identity = None
                self.home_parked = False
            self._publish()

    def main_changed(self, visible):
        with self._lock:
            self.main_visible = bool(visible)
            if not visible:
                self.home_parked = False
            self._publish()

    def submitted(self, activity_id, schedule_version):
        """Only the existing authoritative submission manager calls this method."""
        with self._lock:
            identity = (str(activity_id), int(schedule_version or 1))
            if not ((self.pending and occurrence(self.pending) == identity) or self.panel_identity == identity):
                return
            if self.pending and occurrence(self.pending) == identity:
                self.pending = None
                self.completed = True
                self._completed_identity = identity
            if self.panel_identity == identity:
                self.panel_identity = None
                self.panel_visible = False
            self.home_parked = False
            self._publish()
