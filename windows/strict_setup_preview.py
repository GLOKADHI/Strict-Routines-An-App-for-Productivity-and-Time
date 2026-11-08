"""Short-lived in-memory setup frame for the authenticated desktop bridge."""
import threading
import time


class SetupPreviewCache:
    def __init__(self,clock=time.monotonic):
        self.clock=clock
        self._lock=threading.Lock()
        self._frame=None
        self._received=None

    def update(self,preview):
        if not isinstance(preview,dict):raise ValueError('Invalid setup preview')
        jpeg=preview.get('jpeg')
        if preview.get('current') is True and (not isinstance(jpeg,str) or not jpeg or len(jpeg)>65536):
            raise ValueError('Invalid setup picture')
        with self._lock:
            self._frame=jpeg if preview.get('current') is True else None
            self._received=self.clock()

    def read(self):
        with self._lock:
            age=self.clock()-self._received if self._received is not None else -1
            current=bool(self._frame and 0<=age<1)
            if not current:self._frame=None
            return dict(current=current,jpeg=self._frame if current else None)
