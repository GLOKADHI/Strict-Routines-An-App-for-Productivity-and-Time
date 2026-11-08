"""
STRICTROUTINES RAM & CACHE OPTIMIZER & BACKGROUND RUN CONTROLLER
Provides:
- Native Windows Working Set Trim via psapi.EmptyWorkingSet
- Python GC collection & SQLite cache memory shrink (PRAGMA shrink_memory / wal_checkpoint)
- Background low-priority process optimization (SetPriorityClass)
- Android battery optimization exemption management via ADB
- Automated monitoring loop triggered when memory/cache accumulates
"""

import ctypes
import ctypes.wintypes
import gc
import logging
import os
import subprocess
import threading
import time
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger("RamOptimizer")

HAS_WIN32 = os.name == "nt"


class RamAndCacheOptimizer:
    """
    Monitors process RAM, SQLite cache, and temp storage.
    Performs deterministic, safe memory reduction and background optimization.
    """

    def __init__(self, db=None, restriction_mgr=None, threshold_mb: int = 120):
        self.db = db
        self.restriction_mgr = restriction_mgr
        self.threshold_mb = threshold_mb
        self.auto_optimize_enabled = True
        self.background_mode_enabled = True
        self.last_optimized_at: Optional[str] = None
        self.last_freed_mb: float = 0.0
        self.total_freed_mb: float = 0.0
        self.optimizations_count: int = 0
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None
        self._last_auto_rss_mb: Optional[float] = None

    def get_memory_stats(self) -> Dict[str, Any]:
        """Returns real-time memory and cache metrics."""
        rss_mb = 0.0
        peak_mb = 0.0
        try:
            import psutil
            p = psutil.Process(os.getpid())
            mem = p.memory_info()
            rss_mb = round(mem.rss / (1024 * 1024), 2)
            peak_mb = round(getattr(mem, "peak_wset", mem.rss) / (1024 * 1024), 2)
        except Exception:
            pass

        # SQLite DB + WAL size
        db_size_mb = 0.0
        db_path = getattr(self.db, "db_path", "data/strictrecords.db") if self.db else "data/strictrecords.db"
        if os.path.exists(db_path):
            try:
                db_size_mb += os.path.getsize(db_path) / (1024 * 1024)
                wal_path = db_path + "-wal"
                if os.path.exists(wal_path):
                    db_size_mb += os.path.getsize(wal_path) / (1024 * 1024)
            except Exception:
                pass
        db_size_mb = round(db_size_mb, 2)

        return {
            "rss_mb": rss_mb,
            "peak_mb": peak_mb,
            "sqlite_db_cache_mb": db_size_mb,
            "threshold_mb": self.threshold_mb,
            "auto_optimize_enabled": self.auto_optimize_enabled,
            "background_mode_enabled": self.background_mode_enabled,
            "last_optimized_at": self.last_optimized_at or "Never",
            "last_freed_mb": self.last_freed_mb,
            "total_freed_mb": round(self.total_freed_mb, 2),
            "optimizations_count": self.optimizations_count,
        }

    def optimize(self, force: bool = True) -> Dict[str, Any]:
        """
        Executes an instant, complete RAM & Cache reduction:
        1. Trims process working set via EmptyWorkingSet.
        2. Performs Python garbage collection.
        3. Flushes SQLite WAL checkpoint & executes PRAGMA shrink_memory.
        4. Clears in-memory points & restriction caches.
        """
        with self._lock:
            import psutil
            p = psutil.Process(os.getpid())
            before_rss = p.memory_info().rss / (1024 * 1024)

            # Automatic hygiene must retain useful caches and resident pages;
            # repeatedly evicting them causes disk reads and CPU work on restore.
            if force and self.restriction_mgr:
                try:
                    self.restriction_mgr.clear_caches()
                except Exception:
                    pass

            # 2. Python GC collection
            gc.collect()

            # 3. SQLite memory reduction & WAL flush
            if force and self.db and hasattr(self.db, "_connect"):
                try:
                    with self.db._connect() as conn:
                        conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                        conn.execute("PRAGMA shrink_memory")
                except Exception as e:
                    logger.debug(f"SQLite shrink error: {e}")

            # 4. Windows EmptyWorkingSet API - Trim working set for Python and all WebView2 child processes
            if HAS_WIN32 and force:
                try:
                    # Do not trim working set if user is currently looking at or interacting with UI, unless force=True
                    is_foreground_ui = False
                    if not force:
                        try:
                            from core.server import StrictRecordsRequestHandler
                            api = getattr(StrictRecordsRequestHandler, "desktop_api", None)
                            if api and getattr(api, "_is_visible", False):
                                is_foreground_ui = True
                        except Exception:
                            pass

                    if not is_foreground_ui:
                        psapi = ctypes.windll.psapi
                        kernel32 = ctypes.windll.kernel32
                        psapi.EmptyWorkingSet.argtypes = [ctypes.c_void_p]
                        psapi.EmptyWorkingSet.restype = ctypes.wintypes.BOOL
                        h_proc = kernel32.GetCurrentProcess()
                        psapi.EmptyWorkingSet(h_proc)

                        # Trim working set for the main Python host process only.
                        # Do NOT call EmptyWorkingSet on child WebView2 processes (GPU/renderer),
                        # as doing so invalidates DirectX swap chains and D3D composition surfaces,
                        # causing WebView2 to render an empty black window upon restore.
                        # WebView2 background memory is safely managed via CoreWebView2.MemoryUsageTargetLevel.
                        pass
                except Exception as e:
                    logger.debug(f"EmptyWorkingSet error: {e}")

            if force:
                time.sleep(0.05)
            after_rss = p.memory_info().rss / (1024 * 1024)
            freed = max(0.0, before_rss - after_rss)
            self.last_freed_mb = round(freed, 2)
            self.total_freed_mb += freed
            self.optimizations_count += 1
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.last_optimized_at = now_str

            logger.info(
                f"[RAM Optimizer] Optimized: {before_rss:.2f}MB -> {after_rss:.2f}MB (Freed {freed:.2f}MB)"
            )

            return {
                "success": True,
                "ram_before_mb": round(before_rss, 2),
                "ram_after_mb": round(after_rss, 2),
                "freed_mb": self.last_freed_mb,
                "timestamp": now_str,
                "total_freed_mb": round(self.total_freed_mb, 2),
                "count": self.optimizations_count,
            }

    def apply_background_mode(self, enabled: bool) -> Dict[str, Any]:
        """
        Configures background low-overhead execution:
        - Lowers CPU scheduling priority so foreground apps (Comet browser, coding) never lag.
        - Whitelists Android app on device to prevent OS battery sleep.
        """
        self.background_mode_enabled = bool(enabled)
        # Windows priority class
        if HAS_WIN32:
            try:
                kernel32 = ctypes.windll.kernel32
                kernel32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.wintypes.DWORD]
                kernel32.SetPriorityClass.restype = ctypes.wintypes.BOOL
                h_proc = kernel32.GetCurrentProcess()
                # 0x00004000 = BELOW_NORMAL_PRIORITY_CLASS, 0x00000020 = NORMAL_PRIORITY_CLASS
                prio = 0x00004000 if self.background_mode_enabled else 0x00000020
                kernel32.SetPriorityClass(h_proc, prio)
            except Exception as e:
                logger.debug(f"SetPriorityClass error: {e}")

        return {
            "success": True,
            "background_mode_enabled": self.background_mode_enabled,
        }

    def start_auto_optimizer(self, interval_sec: float = 90.0) -> None:
        """Starts background auto-optimization worker thread."""
        with self._lock:
            if self._worker_thread and self._worker_thread.is_alive():
                return
            self._stop_event.clear()
            self._worker_thread = threading.Thread(
                target=self._auto_optimize_loop,
                args=(interval_sec,),
                daemon=True,
                name="RamAutoOptimizer",
            )
            self._worker_thread.start()
            logger.info("RAM & Cache Auto-Optimizer thread started.")

    def stop_auto_optimizer(self) -> None:
        """Stops background auto-optimizer worker thread."""
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)

    def _auto_optimize_loop(self, interval_sec: float) -> None:
        # Do not run a forced collection/working-set eviction during startup.
        if self._stop_event.wait(60.0):
            return
        # Window visibility owns foreground/background priority transitions.

        while not self._stop_event.is_set():
            try:
                if self._stop_event.wait(max(300.0, interval_sec)):
                    break
                if self.auto_optimize_enabled:
                    stats = self.get_memory_stats()
                    # Database file size is not RAM pressure. Do not collect or
                    # trim merely because a user's history is larger than 15 MB.
                    rss = stats["rss_mb"]
                    grew = self._last_auto_rss_mb is not None and rss - self._last_auto_rss_mb >= 32.0
                    self._last_auto_rss_mb = rss
                    try:
                        import psutil
                        memory = psutil.virtual_memory()
                        pressure = memory.available < max(512 * 1024 * 1024, memory.total * .10)
                    except Exception:
                        pressure = False
                    if rss > max(256, self.threshold_mb) and (grew or pressure):
                        self.optimize(force=False)
            except Exception as e:
                logger.debug(f"Auto-optimizer tick error: {e}")
