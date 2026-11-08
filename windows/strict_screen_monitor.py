"""Memory-only desktop capture health during Strict Session; no recordings/uploads.
Automatically pauses desktop screen capture when Sensitive Page Protection is active."""
import threading
import time

from windows.strict_sensitive_page_protection import SensitivePageProtectionManager


class StrictScreenMonitor:
    def __init__(self, capture=None, clock=time.monotonic):
        self.capture = capture or self._capture
        self.clock = clock
        self.stop_event = threading.Event()
        self.thread = None
        self.last_frame = None
        self.resolution = None
        self.error = 'NOT_STARTED'
        self.suppressed_captures = 0

    @staticmethod
    def _capture():
        from PIL import ImageGrab
        return ImageGrab.grab(all_screens=True)

    def sample(self):
        # Enforce Sensitive Page Protection Privacy Shield:
        # Never capture desktop screenshots while visual monitoring is paused.
        try:
            spp = SensitivePageProtectionManager.get_instance()
            if spp.is_visual_monitoring_paused():
                st = spp.get_status()
                self.last_frame = None
                self.error = st.get('display_banner') or 'SENSITIVE PAGE PROTECTION ACTIVE'
                self.suppressed_captures += 1
                return False
        except Exception:
            pass

        image = None
        try:
            image = self.capture()
            if image is None or min(image.size) < 64:
                raise ValueError('Screen capture unavailable')
            self.resolution = f'{image.width} × {image.height}'
            # Pixels are discarded immediately. Only capture health is retained.
            self.last_frame = self.clock()
            self.error = ''
            return True
        except Exception:
            self.last_frame = None
            self.error = 'Screen capture unavailable. Return to the Windows session.'
            return False
        finally:
            if image is not None:
                image.close()

    def start(self):
        if self.thread and self.thread.is_alive():
            return self.status()
        self.stop_event.clear()
        self.sample()

        def run():
            while not self.stop_event.wait(1.):
                self.sample()

        self.thread = threading.Thread(target=run, name='StrictSessionScreenMonitor', daemon=True)
        self.thread.start()
        return self.status()

    def status(self):
        try:
            spp = SensitivePageProtectionManager.get_instance()
            if spp.is_visual_monitoring_paused():
                st = spp.get_status()
                self.last_frame = None
                return dict(
                    screen_capture_active=False,
                    privacy_shield_active=True,
                    visual_monitoring_paused=True,
                    screen_monitoring_status=st.get('protection_state', 'SENSITIVE_PAGE_PROTECTION_ACTIVE'),
                    screen_capture_fps=0,
                    screen_capture_age_seconds=None,
                    primary_resolution=self.resolution,
                    screen_monitoring_message=st.get('display_banner', 'SENSITIVE PAGE PROTECTION ACTIVE'),
                    cooldown_remaining_seconds=st.get('cooldown_remaining_seconds', 0),
                    cooldown_formatted=st.get('cooldown_formatted', '00:00'),
                    sensitive_page_category=st.get('current_category'),
                    suppressed_captures=self.suppressed_captures,
                )
            elif self.thread and self.thread.is_alive() and self.last_frame is None:
                # Immediately re-sample upon privacy cooldown completion so capture resumes seamlessly
                self.sample()
        except Exception:
            pass

        fresh = bool(
            self.thread
            and self.thread.is_alive()
            and self.last_frame is not None
            and 0 <= self.clock() - self.last_frame < 3.
        )
        return dict(
            screen_capture_active=fresh,
            privacy_shield_active=False,
            visual_monitoring_paused=False,
            screen_monitoring_status='ACTIVE' if fresh else 'UNVERIFIED',
            screen_capture_fps=1 if fresh else 0,
            screen_capture_age_seconds=None if self.last_frame is None else max(0, self.clock() - self.last_frame),
            primary_resolution=self.resolution,
            screen_monitoring_message=self.error,
            cooldown_remaining_seconds=0,
            cooldown_formatted='00:00',
            sensitive_page_category=None,
            suppressed_captures=self.suppressed_captures,
        )

    def stop(self):
        self.stop_event.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(2.)
        self.last_frame = None
        self.resolution = None
        self.error = 'STOPPED'
