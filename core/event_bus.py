"""
StrictRecords Thread-Safe Real-Time EventBus
Shared pub/sub event bus for real-time synchronization between Windows, Android,
TimeWindowEngine, SubmissionWindowManager, and WebView2 SSE/Polling clients.
"""

from collections import deque
from datetime import datetime
import queue
import threading
from typing import Any, Dict, List, Optional
import uuid


class EventBus:
    """Thread-safe pub/sub event bus for real-time synchronization between Windows and Android."""
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "EventBus":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self):
        self._subscribers: List[queue.Queue] = []
        self._history: deque = deque(maxlen=200)
        self._sub_lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q = queue.Queue(maxsize=100)
        with self._sub_lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        with self._sub_lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def publish(self, event_type: str, payload: Dict[str, Any], event_id: Optional[str] = None) -> Dict[str, Any]:
        evt = {
            "event_id": event_id or f"evt_{uuid.uuid4().hex[:12]}",
            "event_type": event_type,
            "timestamp": datetime.now().isoformat(),
            **payload,
        }
        with self._sub_lock:
            self._history.append(evt)
            subscribers_copy = list(self._subscribers)

        for q in subscribers_copy:
            try:
                q.put_nowait(evt)
            except queue.Full:
                pass
        return evt

    def get_events_since(self, since_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        with self._sub_lock:
            history_list = list(self._history)
        if not since_id:
            return history_list[-limit:]
        res = []
        found = False
        for item in history_list:
            if found:
                res.append(item)
            elif item.get("event_id") == since_id:
                found = True
        return res if found else history_list[-limit:]
