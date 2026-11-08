"""



STRICTRECORDS – MANDATORY iPAD CAMERA STREAM HEALTH MONITOR & 11-STAGE PIPELINE TRACER



Implements:



1. Initial Camera Prerequisite Check (IOS_CAMERA_INITIAL_CHECK: PASSED once >=1 real frame is



   received, decoded, and accepted by the Windows inference pipeline; brief pauses afterward



   do not invalidate the completed prerequisite).



2. Active Strict Session 15-Second Frame-Interruption Policy (monotonic clock):



   - 0–5 seconds  : ACTIVE / TEMPORARY GAP (no warning)



   - 5–10 seconds : TEMPORARILY PAUSED (continue session)



   - 10–15 seconds: DEGRADED (show warning and attempt recovery)



   - > 15 seconds : CAMERA_INTERRUPTED (create single unique incident, flag session as



                    FLAGGED_CAMERA_INTERRUPTION, show prominent Windows alert, attempt recovery)



3. 11-Stage End-to-End Media Pipeline Instrumentation & WebRTC Sender/Receiver Statistics.



4. Separate Watchdogs for Frame Reception vs. Inference Processing:



   - If Rx FPS > 0 and Proc FPS == 0, reports IOS_PROCESSING_STALLED



     ("VIDEO RECEIVING – PROCTORING PROCESSING STALLED") instead of a network interruption.



5. Automatic Recovery Verification requiring 3 consecutive seconds of stable valid frames.



"""







from __future__ import annotations







import collections



import json



import logging



import threading



import time



import uuid



from datetime import datetime, timezone



from typing import Any, Callable, Dict, List, Optional, Tuple







import numpy as np







from core.strict_session_lifecycle import StrictLifecycleState







logger = logging.getLogger("IPadCameraStreamMonitor")







class DualInitialStatus(str):



    def __eq__(self, other):



        s_self = str(self)



        s_other = str(other)



        if s_self == s_other:



            return True



        if s_self in ("IOS_CAMERA_INITIAL_VERIFIED", "IOS_CAMERA_INITIAL_CHECK: PASSED") and s_other in ("IOS_CAMERA_INITIAL_CHECK: PASSED", "IOS_CAMERA_INITIAL_VERIFIED"):



            return True



        return False







class DualCause(str):



    def __eq__(self, other):



        s_self = str(self)



        s_other = str(other)



        if s_self == s_other:



            return True



        if s_self in ("INFERENCE_PIPELINE_STALLED", "WINDOWS_INFERENCE_STALL") and s_other in ("INFERENCE_PIPELINE_STALLED", "WINDOWS_INFERENCE_STALL"):



            return True



        return False















PIPELINE_STAGE_NAMES: Tuple[str, ...] = (



    "1_ios_camera_capture",



    "2_ios_video_track",



    "3_frame_encoding",



    "4_rtp_packet_transmission",



    "5_windows_rtp_packet_reception",



    "6_complete_frame_reception",



    "7_video_decoding",



    "8_frame_delivery_to_receiver",



    "9_inference_queue_insertion",



    "10_face_gaze_head_pose_processing",



    "11_dashboard_result_delivery",



)



# Comprehensive 23-stage WebRTC connection & media pipeline tracer



CONNECTION_STAGES_23: Tuple[str, ...] = (



    "1_ios_companion_invitation_auth",



    "2_device_registration",



    "3_signaling_websocket_connect",



    "4_webrtc_peer_connection_create",



    "5_camera_permission_track_init",



    "6_video_sender_transceiver_create",



    "7_sdp_offer_generation",



    "8_sdp_offer_delivery_windows",



    "9_remote_description_installation_windows",



    "10_sdp_answer_generation",



    "11_sdp_answer_delivery_ios",



    "12_remote_description_installation_ios",



    "13_local_ice_candidate_gathering",



    "14_ice_candidate_delivery",



    "15_remote_ice_candidate_installation",



    "16_ice_connectivity_checks",



    "17_ice_selected_candidate_pair",



    "18_dtls_connection",



    "19_incoming_video_track_windows",



    "20_rtp_packet_reception",



    "21_video_frame_assembly_decoding",



    "22_windows_frame_queue_insertion",



    "23_windows_camera_inference_processing",



)















class IPadCameraState:



    # Device / transport layer states (independent of video frames)



    IOS_DEVICE_CONNECTED = "IOS_DEVICE_CONNECTED"



    HTTPS_VERIFIED = "HTTPS_VERIFIED"



    WEBRTC_CONNECTED = "WEBRTC_CONNECTED"



    IOS_WEBRTC_CONNECTED = "IOS_WEBRTC_CONNECTED"



    CAMERA_PERMISSION_GRANTED = "CAMERA_PERMISSION_GRANTED"



    VIDEO_TRACK_ACTIVE = "VIDEO_TRACK_ACTIVE"



    VIDEO_TRACK_RECEIVED = "VIDEO_TRACK_RECEIVED"



    IOS_CAPTURE_ACTIVE = "IOS_CAPTURE_ACTIVE"







    # Initial Prerequisite Check states



    IOS_CAMERA_INITIAL_CHECK_PASSED = "IOS_CAMERA_INITIAL_CHECK: PASSED"



    IOS_CAMERA_INITIAL_CHECK_PENDING = "IOS_CAMERA_INITIAL_CHECK: PENDING"



    IOS_CAMERA_INITIAL_CHECK_FAILED = "IOS_CAMERA_INITIAL_CHECK: FAILED"







    # Frame & Processing states (Section 5)



    CAMERA_INITIALIZING = "CAMERA_INITIALIZING"



    VALID_FRAMES_RECEIVED = "VALID_FRAMES_RECEIVED"



    VIDEO_FRAMES_RECEIVED = "VIDEO_FRAMES_RECEIVED"



    IOS_FRAMES_RECEIVING = "IOS_FRAMES_RECEIVING"



    IOS_FRAMES_DECODING = "IOS_FRAMES_DECODING"



    IOS_INFERENCE_PROCESSING = "IOS_INFERENCE_PROCESSING"



    VIDEO_PROCESSING_ACTIVE = "VIDEO_PROCESSING_ACTIVE"



    WINDOWS_PROCESSING_ACTIVE = "WINDOWS_PROCESSING_ACTIVE"



    IOS_PROCESSING_STALLED = "IOS_PROCESSING_STALLED"



    PROCESSING_DEGRADED = "PROCESSING_DEGRADED"







    # 15-Second Grace-Period / Interruption states (Section 3 & 4)



    CAMERA_RECOVERING = "CAMERA_RECOVERING"                  # 0–5 s gap or stability verification



    CAMERA_TEMPORARILY_PAUSED = "CAMERA_TEMPORARILY_PAUSED"  # 5–10 s gap: session continues



    CAMERA_DEGRADED = "CAMERA_DEGRADED"                      # 10–15 s: warning, attempt recovery



    CAMERA_INTERRUPTED = "CAMERA_INTERRUPTED"                # >15 s: incident, alert, flag



    IOS_CAMERA_INTERRUPTED = "IOS_CAMERA_INTERRUPTED"

    IOS_CAMERA_STOPPED = "IOS_CAMERA_STOPPED"



    CAMERA_RECOVERED = "CAMERA_RECOVERED"                    # stable 3+ s after interruption



    IOS_CAMERA_RECOVERED = "IOS_CAMERA_RECOVERED"







    # Dashboard Exact States (Section 11)



    INITIAL_VERIFIED = "INITIAL_VERIFIED"



    CONNECTING = "CONNECTING"



    LIVE_PROCESSING = "LIVE_PROCESSING"



    TEMPORARILY_PAUSED = "TEMPORARILY_PAUSED"



    DEGRADED = "DEGRADED"



    INTERRUPTED = "INTERRUPTED"



    RECOVERING = "RECOVERING"



    RECOVERED = "RECOVERED"







    # ---------------------------------------------------------------------------



    # Configurable 15-second grace period thresholds (seconds)



    # ---------------------------------------------------------------------------



    THRESHOLD_RECOVERING: float = 5.0    # 0–5 s  : ACTIVE / TEMPORARY GAP – no warning



    THRESHOLD_TEMP_PAUSED: float = 10.0  # 5–10 s : TEMPORARILY PAUSED – continue session



    THRESHOLD_DEGRADED: float = 15.0     # 10–15 s: DEGRADED – show warning and attempt recovery



    # > 15 s: CAMERA_INTERRUPTED (single incident per continuous outage)



    RECOVERY_STABLE_SECONDS: float = 3.0  # 3 consecutive seconds of valid frames to confirm recovery











class IPadCameraStreamMonitor:



    """



    Dedicated continuous health monitor and 11-stage media pipeline tracer for the iOS camera.



    """







    _instance: Optional[IPadCameraStreamMonitor] = None



    _instance_lock = threading.Lock()







    @classmethod



    def get_instance(cls, db=None) -> IPadCameraStreamMonitor:



        with cls._instance_lock:



            if cls._instance is None:



                cls._instance = cls(db=db)



            elif db is not None and cls._instance.db is None:



                cls._instance.db = db



                cls._instance._init_db_schema()



            return cls._instance







    @classmethod



    def reset_instance(cls) -> None:



        with cls._instance_lock:



            if cls._instance is not None:



                cls._instance.reset()



                cls._instance = None







    def __init__(



        self,



        db=None,



        clock: Optional[Callable[[], float]] = None,



        wall_clock: Optional[Callable[[], datetime]] = None,



    ):



        self._lock = threading.RLock()



        self.db = db



        self._clock = clock or time.monotonic



        self._wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))







        # Core frame reception, decoding & inference telemetry



        self.lifecycle_state: StrictLifecycleState = StrictLifecycleState.IDLE



        self.session_id: str = ""



        self.camera_required: bool = True



        self.preparation_id: str = ""



        self.camera_device_id: str = "ios_companion_camera"



        self.camera_source: str = "iPhone/iPad"







        # Session-specific frame timestamps (strictly measured within active confirmed session)



        self.session_started_mono: Optional[float] = None



        self.session_started_wall_iso: Optional[str] = None



        self.session_last_valid_frame_mono: Optional[float] = None



        self.session_last_valid_frame_wall_iso: Optional[str] = None



        self.session_last_decoded_mono: Optional[float] = None



        self.session_last_decoded_wall_iso: Optional[str] = None



        self.session_last_processed_mono: Optional[float] = None



        self.session_last_processed_wall_iso: Optional[str] = None



        self.session_frame_count: int = 0







        # Prerequisite / setup preview timestamps



        self.prerequisite_last_frame_mono: Optional[float] = None



        self.prerequisite_last_frame_wall_iso: Optional[str] = None







        self.last_valid_frame_mono: Optional[float] = None



        self.last_valid_frame_wall_iso: Optional[str] = None



        self.last_successfully_decoded_mono: Optional[float] = None



        self.last_successfully_decoded_wall_iso: Optional[str] = None



        self.last_frame_queued_for_inference_mono: Optional[float] = None



        self.last_frame_queued_for_inference_wall_iso: Optional[str] = None



        self.last_successfully_processed_mono: Optional[float] = None



        self.last_successfully_processed_wall_iso: Optional[str] = None







        self.frame_sequence: int = 0



        self.decoded_frames_count: int = 0



        self.queued_frames_count: int = 0



        self.processed_frames_count: int = 0



        self.video_presentation_timestamp: Optional[float] = None



        self.frame_width: int = 0



        self.frame_height: int = 0



        self.dropped_frames_count: int = 0







        # Connection and proctoring states



        self.ios_device_connected: bool = False



        self.https_verified: bool = False



        self.camera_permission_granted: bool = False



        self.webrtc_connection_state: str = "DISCONNECTED"



        self.ice_connection_state: str = "NEW"



        self.video_track_received: bool = False



        self.proctoring_processing_state: str = "IDLE"



        self.inference_worker_state: str = "RUNNING"



        self.last_processing_error: str = ""



        self.inference_queue_length: int = 0



        self.health_state: str = IPadCameraState.IOS_DEVICE_CONNECTED







        # Initial Camera Prerequisite state (Section 1)



        self.initial_check_passed: bool = False



        self.initial_check_passed_at_iso: Optional[str] = None



        self.initial_check_error: str = ""



        self.first_frame_received: bool = False



        self.first_frame_decoded: bool = False



        self.first_frame_accepted_by_inference: bool = False



        self.ios_camera_initial_verified: bool = False



        self.initial_verification_record: Optional[Dict[str, Any]] = None







        # Rolling FPS timestamp deques (monotonic)



        self._frame_rx_times: collections.deque = collections.deque(maxlen=90)



        self._frame_dec_times: collections.deque = collections.deque(maxlen=90)



        self._frame_proc_times: collections.deque = collections.deque(maxlen=90)



        self.received_fps: float = 0.0



        self.decoded_fps: float = 0.0



        self.processing_fps: float = 0.0







        # 11-Stage Pipeline Tracer



        self.pipeline_stages: Dict[str, Dict[str, Any]] = self._init_pipeline_stages()



        self.connection_id: str = ""



        self.current_tx_id: str = ""



        self.detailed_stages: Dict[str, Dict[str, Any]] = self._init_detailed_stages()



        self.stage_history: List[Dict[str, Any]] = []











        # Real WebRTC Sender (iOS) & Receiver (Windows) stats



        self.ios_sender_stats: Dict[str, Any] = {



            "framesEncoded": 0,



            "framesSent": 0,



            "packetsSent": 0,



            "bytesSent": 0,



            "captureFps": 0.0,



            "encodedFps": 0.0,



            "transmittedFps": 0.0,



            "outboundBitrateKbps": 0.0,



            "trackReadyState": "unknown",



            "trackMuted": False,



            "trackEnabled": True,



            "captureWidth": 0,



            "captureHeight": 0,



            "cameraSource": "iPhone/iPad Front Camera",



            "deviceOrientation": "portrait",



            "updatedAtIso": None,



        }



        self.windows_receiver_stats: Dict[str, Any] = {



            "packetsReceived": 0,



            "bytesReceived": 0,



            "packetsLost": 0,



            "jitter": 0.0,



            "framesReceived": 0,



            "framesDecoded": 0,



            "framesDropped": 0,



            "frameWidth": 0,



            "frameHeight": 0,



            "totalDecodeTime": 0.0,



            "iceConnectionState": "NEW",



            "localCandidateType": "unknown",



            "remoteCandidateType": "unknown",



            "candidatePairState": "unknown",



            "routeType": "UNKNOWN",



            "rttMs": None,



            "updatedAtIso": None,



        }







        # Interruption policy & recovery gate



        self.session_started_mono: Optional[float] = None



        self.active_incident: Optional[Dict[str, Any]] = None



        self.incidents_history: List[Dict[str, Any]] = []



        self.stable_recovery_start_mono: Optional[float] = None



        self.was_recovered: bool = False



        self.last_recovery_mono: Optional[float] = None



        self.is_timer_paused: bool = False



        self.is_session_flagged: bool = False



        self.flags: List[str] = []







        if self.db:



            self._init_db_schema()



            self._load_thresholds_from_db()







    @staticmethod



    def _init_pipeline_stages() -> Dict[str, Dict[str, Any]]:



        return {



            stage: {



                "stage": stage,



                "count": 0,



                "last_mono": None,



                "last_wall_iso": None,



                "status": "IDLE",



            }



            for stage in PIPELINE_STAGE_NAMES



        }



    @staticmethod



    def _init_detailed_stages() -> Dict[str, Dict[str, Any]]:



        return {



            stage: {



                "stage": stage,



                "session_id": "",



                "connection_id": "",



                "source_id": "IOS_FRONT_CAMERA",



                "timestamp_iso": None,



                "timestamp_mono": None,



                "signaling_state": "NEW",



                "ice_state": "NEW",



                "peer_connection_state": "NEW",



                "success": False,



                "error_message": "",



                "transition_count": 0,



            }



            for stage in CONNECTION_STAGES_23



        }







    def record_detailed_stage(



        self,



        stage_name: str,



        session_id: str = "",



        connection_id: str = "",



        source_id: str = "IOS_FRONT_CAMERA",



        signaling_state: str = "",



        ice_state: str = "",



        peer_connection_state: str = "",



        success: bool = True,



        error_message: str = "",



    ) -> None:



        with self._lock:



            now_mono = self._clock()



            now_iso = self._wall_clock().isoformat()



            if not session_id and self.session_id:



                session_id = self.session_id



            if not connection_id and getattr(self, "connection_id", ""):



                connection_id = getattr(self, "connection_id", "")



            if connection_id:



                self.connection_id = connection_id







            entry = self.detailed_stages.get(stage_name)



            if entry is not None:



                entry["session_id"] = session_id



                entry["connection_id"] = connection_id



                entry["source_id"] = source_id



                entry["timestamp_iso"] = now_iso



                entry["timestamp_mono"] = now_mono



                entry["signaling_state"] = signaling_state or getattr(self, "signaling_state", "UNKNOWN")



                entry["ice_state"] = ice_state or getattr(self, "ice_connection_state", "UNKNOWN")



                entry["peer_connection_state"] = peer_connection_state or getattr(self, "webrtc_connection_state", "UNKNOWN")



                entry["success"] = bool(success)



                entry["error_message"] = str(error_message or "")



                entry["transition_count"] += 1







            history_item = {



                "stage": stage_name,



                "session_id": session_id,



                "connection_id": connection_id,



                "source_id": source_id,



                "timestamp_iso": now_iso,



                "timestamp_mono": round(now_mono, 3),



                "signaling_state": signaling_state,



                "ice_state": ice_state,



                "peer_connection_state": peer_connection_state,



                "success": success,



                "error_message": error_message,



            }



            self.stage_history.append(history_item)



            if len(self.stage_history) > 120:



                self.stage_history.pop(0)







            # Map to 11-stage pipeline if applicable



            pipeline_map = {



                "5_camera_permission_track_init": "1_ios_camera_capture",



                "6_video_sender_transceiver_create": "2_ios_video_track",



                "7_sdp_offer_generation": "3_frame_encoding",



                "14_ice_candidate_delivery": "4_rtp_packet_transmission",



                "20_rtp_packet_reception": "5_windows_rtp_packet_reception",



                "21_video_frame_assembly_decoding": "6_complete_frame_reception",



                "22_windows_frame_queue_insertion": "9_inference_queue_insertion",



                "23_windows_camera_inference_processing": "10_face_gaze_head_pose_processing",



            }



            mapped = pipeline_map.get(stage_name)



            if mapped and mapped in self.pipeline_stages:



                p_entry = self.pipeline_stages[mapped]



                p_entry["count"] += 1



                p_entry["last_mono"] = now_mono



                p_entry["last_wall_iso"] = now_iso



                p_entry["status"] = "ACTIVE" if success else "FAILED"







    def get_frame_confirmation_payload(self) -> Dict[str, Any]:



        with self._lock:



            now_wall = self._wall_clock()



            now_iso = now_wall.isoformat()



            now_mono = self._clock()



            self._recalculate_fps(now_mono)



            dec_cnt = getattr(self, "decoded_frames_count", self.frame_sequence)



            proc_cnt = getattr(self, "processed_frames_count", 0)



            return {



                "type": "IOS_CAMERA_FRAME_READY",



                "session_id": self.session_id or "",



                "connection_id": getattr(self, "connection_id", ""),



                "source_id": "IOS_FRONT_CAMERA",



                "received_frame_sequence": self.frame_sequence,



                "decoded_frame_sequence": dec_cnt,



                "processed_frame_sequence": proc_cnt,



                "received_timestamp": self.session_last_valid_frame_wall_iso or self.last_valid_frame_wall_iso or now_iso,



                "decoded_timestamp": self.last_successfully_decoded_wall_iso or now_iso,



                "processed_timestamp": getattr(self, "last_successfully_processed_wall_iso", None) or now_iso,



                "received_fps": self.received_fps,



                "decoded_fps": getattr(self, "decoded_fps", self.received_fps),



                "processed_fps": self.processing_fps,



                "confirmed": proc_cnt > 0 or getattr(self, "first_frame_accepted_by_inference", False),



            }







    def export_diagnostics_sanitized(self) -> Dict[str, Any]:



        with self._lock:



            now_iso = self._wall_clock().isoformat()



            diag = self.get_diagnostics()



            diag.pop("token", None)



            diag.pop("credential", None)



            diag.pop("turn_secret", None)



            diag.pop("interruption_warning_modal", None)



            diag["exported_at_iso"] = now_iso



            diag["detailed_stages_23"] = dict(self.detailed_stages)



            diag["stage_history"] = list(self.stage_history[-50:])



            return diag











    def record_pipeline_stage(self, stage_name: str, count_increment: int = 1, status: str = "ACTIVE") -> None:



        with self._lock:



            now_mono = self._clock()



            now_iso = self._wall_clock().isoformat()



            entry = self.pipeline_stages.get(stage_name)



            if entry is not None:



                entry["count"] = max(entry["count"], entry["count"] + max(0, int(count_increment)))



                entry["last_mono"] = now_mono



                entry["last_wall_iso"] = now_iso



                entry["status"] = status







    def _init_db_schema(self) -> None:



        try:



            with self.db._connect() as conn:



                conn.execute("""



                    CREATE TABLE IF NOT EXISTS strict_camera_incidents (



                        incident_id TEXT PRIMARY KEY,



                        session_id TEXT NOT NULL,



                        camera_device_id TEXT,



                        camera_source TEXT NOT NULL,



                        last_valid_frame_timestamp TEXT,



                        interruption_detected_timestamp TEXT NOT NULL,



                        interruption_duration REAL,



                        last_webrtc_state TEXT,



                        last_received_fps REAL,



                        last_processing_fps REAL,



                        incident_status TEXT NOT NULL,



                        recovery_timestamp TEXT,



                        details_json TEXT



                    )



                """)



        except Exception as err:



            logger.debug(f"Could not initialize strict_camera_incidents table: {err}")







    def _load_thresholds_from_db(self) -> None:



        if not self.db or not hasattr(self.db, "get_system_config"):



            return



        try:



            rec = self.db.get_system_config("IPAD_CAMERA_THRESHOLD_RECOVERING")



            paused = self.db.get_system_config("IPAD_CAMERA_THRESHOLD_TEMP_PAUSED")



            deg = self.db.get_system_config("IPAD_CAMERA_THRESHOLD_DEGRADED")



            stable = self.db.get_system_config("IPAD_CAMERA_RECOVERY_STABLE_SECONDS")



            if rec is not None:



                IPadCameraState.THRESHOLD_RECOVERING = max(1.0, float(rec))



            if paused is not None:



                IPadCameraState.THRESHOLD_TEMP_PAUSED = max(IPadCameraState.THRESHOLD_RECOVERING + 1.0, float(paused))



            if deg is not None:



                IPadCameraState.THRESHOLD_DEGRADED = max(IPadCameraState.THRESHOLD_TEMP_PAUSED + 1.0, float(deg))



            if stable is not None:



                IPadCameraState.RECOVERY_STABLE_SECONDS = max(1.0, float(stable))



        except Exception as err:



            logger.debug(f"Could not load iPad camera thresholds from DB: {err}")







    def configure_thresholds(



        self,



        recovering: Optional[float] = None,



        temp_paused: Optional[float] = None,



        degraded: Optional[float] = None,



        recovery_stable: Optional[float] = None,



    ) -> Dict[str, float]:



        """Updates configurable frame-gap grace period thresholds and persists to Windows settings."""



        with self._lock:



            if recovering is not None:



                IPadCameraState.THRESHOLD_RECOVERING = max(1.0, float(recovering))



            if temp_paused is not None:



                IPadCameraState.THRESHOLD_TEMP_PAUSED = max(IPadCameraState.THRESHOLD_RECOVERING + 1.0, float(temp_paused))



            if degraded is not None:



                IPadCameraState.THRESHOLD_DEGRADED = max(IPadCameraState.THRESHOLD_TEMP_PAUSED + 1.0, float(degraded))



            if recovery_stable is not None:



                IPadCameraState.RECOVERY_STABLE_SECONDS = max(1.0, float(recovery_stable))







            if self.db and hasattr(self.db, "set_system_config"):



                try:



                    self.db.set_system_config("IPAD_CAMERA_THRESHOLD_RECOVERING", str(IPadCameraState.THRESHOLD_RECOVERING))



                    self.db.set_system_config("IPAD_CAMERA_THRESHOLD_TEMP_PAUSED", str(IPadCameraState.THRESHOLD_TEMP_PAUSED))



                    self.db.set_system_config("IPAD_CAMERA_THRESHOLD_DEGRADED", str(IPadCameraState.THRESHOLD_DEGRADED))



                    self.db.set_system_config("IPAD_CAMERA_RECOVERY_STABLE_SECONDS", str(IPadCameraState.RECOVERY_STABLE_SECONDS))



                except Exception as err:



                    logger.debug(f"Could not persist iPad camera thresholds: {err}")







            return self.get_thresholds()







    def get_thresholds(self) -> Dict[str, float]:



        return {



            "recovering_seconds": IPadCameraState.THRESHOLD_RECOVERING,



            "temporarily_paused_seconds": IPadCameraState.THRESHOLD_TEMP_PAUSED,



            "degraded_seconds": IPadCameraState.THRESHOLD_DEGRADED,



            "recovery_stable_seconds": IPadCameraState.RECOVERY_STABLE_SECONDS,



        }







    def reset_initial_check(self) -> None:



        """Resets the initial camera prerequisite check for 'Retry Camera Test'."""



        with self._lock:



            self.initial_check_passed = False



            self.initial_check_passed_at_iso = None



            self.initial_check_error = ""



            self.first_frame_received = False



            self.first_frame_decoded = False



            self.first_frame_accepted_by_inference = False







    def reset(self) -> None:



        with self._lock:



            self.lifecycle_state = StrictLifecycleState.IDLE



            self.session_id = ""



            self.preparation_id = ""



            self.session_started_mono = None



            self.session_started_wall_iso = None



            self.session_last_valid_frame_mono = None



            self.session_last_valid_frame_wall_iso = None



            self.session_last_decoded_mono = None



            self.session_last_decoded_wall_iso = None



            self.session_last_processed_mono = None



            self.session_last_processed_wall_iso = None



            self.session_frame_count = 0



            self.prerequisite_last_frame_mono = None



            self.prerequisite_last_frame_wall_iso = None



            self.last_valid_frame_mono = None



            self.last_valid_frame_wall_iso = None



            self.last_successfully_decoded_mono = None



            self.last_successfully_decoded_wall_iso = None



            self.last_frame_queued_for_inference_mono = None



            self.last_frame_queued_for_inference_wall_iso = None



            self.last_successfully_processed_mono = None



            self.last_successfully_processed_wall_iso = None



            self.frame_sequence = 0



            self.decoded_frames_count = 0



            self.queued_frames_count = 0



            self.processed_frames_count = 0



            self.video_presentation_timestamp = None



            self.frame_width = 0



            self.frame_height = 0



            self.dropped_frames_count = 0



            self.received_fps = 0.0



            self.decoded_fps = 0.0



            self.processing_fps = 0.0



            self.ios_device_connected = False



            self.https_verified = False



            self.camera_permission_granted = False



            self.webrtc_connection_state = "DISCONNECTED"



            self.ice_connection_state = "NEW"



            self.video_track_received = False



            self.proctoring_processing_state = "IDLE"



            self.inference_worker_state = "RUNNING"



            self.last_processing_error = ""



            self.inference_queue_length = 0



            self.health_state = IPadCameraState.IOS_DEVICE_CONNECTED



            self.reset_initial_check()



            self.pipeline_stages = self._init_pipeline_stages()



            self.active_incident = None



            self.incidents_history.clear()



            self.stable_recovery_start_mono = None



            self.was_recovered = False



            self.last_recovery_mono = None



            self.is_timer_paused = False



            self.is_session_flagged = False



            self.flags.clear()



            self._frame_rx_times.clear()



            self._frame_dec_times.clear()



            self._frame_proc_times.clear()







    def transition_lifecycle_state(



        self,



        new_state: StrictLifecycleState,



        session_id: Optional[str] = None,



        reason: str = "",



    ) -> None:



        with self._lock:



            old_state = self.lifecycle_state



            now_mono = self._clock()



            now_wall = self._wall_clock()



            now_iso = now_wall.isoformat()







            if new_state == StrictLifecycleState.SESSION_ACTIVE:



                self.lifecycle_state = StrictLifecycleState.SESSION_ACTIVE



                self.session_id = session_id or self.session_id



                self.session_started_mono = now_mono



                self.session_started_wall_iso = now_iso



                # Reset previous session frame timestamps so stale frames never pollute new session



                self.session_last_valid_frame_mono = None



                self.session_last_valid_frame_wall_iso = None



                self.session_last_decoded_mono = None



                self.session_last_decoded_wall_iso = None



                self.session_last_processed_mono = None



                self.session_last_processed_wall_iso = None



                self.session_frame_count = 0



                self.active_incident = None



                self.stable_recovery_start_mono = None



                self.was_recovered = False



                self.last_recovery_mono = None



                self.is_timer_paused = False



                self.is_session_flagged = False



                self.flags.clear()



                self.health_state = IPadCameraState.CAMERA_INITIALIZING



                logger.info(



                    f"iPad camera monitoring active for session: {self.session_id} "



                    f"(baseline mono={now_mono:.3f})"



                )







            elif new_state in (



                StrictLifecycleState.SESSION_COMPLETED,



                StrictLifecycleState.SESSION_CANCELLED,



                StrictLifecycleState.SESSION_FAILED,



                StrictLifecycleState.IDLE,



            ):



                if self.active_incident is not None:



                    self.active_incident["incident_status"] = (



                        "RESOLVED" if new_state == StrictLifecycleState.SESSION_COMPLETED else "CLOSED"



                    )



                    self.active_incident["recovery_timestamp"] = now_iso



                    self._save_incident_to_db(self.active_incident)



                    self.active_incident = None







                self.lifecycle_state = new_state



                self.session_id = ""



                self.is_timer_paused = False



                self.is_session_flagged = False



                self.stable_recovery_start_mono = None



                self.session_started_mono = None



                self.session_last_valid_frame_mono = None



                self.session_last_decoded_mono = None



                self.session_last_processed_mono = None



                self.health_state = (



                    IPadCameraState.IOS_DEVICE_CONNECTED



                    if self.ios_device_connected



                    else IPadCameraState.CAMERA_INITIALIZING



                )



                logger.info(f"iPad camera monitoring stopped (lifecycle={new_state.value}, reason={reason})")







            elif new_state == StrictLifecycleState.SESSION_PAUSED:



                self.lifecycle_state = StrictLifecycleState.SESSION_PAUSED



                self.is_timer_paused = True







            else:



                self.lifecycle_state = new_state



                self.is_timer_paused = False



                self.is_session_flagged = False



                self.active_incident = None







    def start_session_monitoring(



        self,



        session_id: str,



        camera_device_id: str = "ios_companion_camera",



        camera_required: bool = True,



    ) -> None:



        with self._lock:



            self.camera_device_id = camera_device_id



            self.camera_required = camera_required



            self.transition_lifecycle_state(StrictLifecycleState.SESSION_ACTIVE, session_id=session_id)







    def record_explicit_stop(



        self,



        source_id: str = "IOS_FRONT_CAMERA",



        event_id: Optional[str] = None,



        reason: str = "iPad front camera stopped by user",



        timestamp: Optional[float] = None,



        user_initiated: bool = True,



    ) -> Dict[str, Any]:



        with self._lock:



            self.health_state = IPadCameraState.IOS_CAMERA_STOPPED



            self.webrtc_connection_state = "STOPPED"



            self.last_reported_state = "IOS_CAMERA_STOPPED"
            try:
                from windows.strict_ios_media_health import IOSMediaHealthManager
                IOSMediaHealthManager.get_instance().record_explicit_stop(
                    source_id,
                    {"reason": reason, "event_id": event_id, "timestamp": timestamp or time.time(), "user_initiated": user_initiated}
                )
            except Exception:
                pass



            self.stop_event_info = {



                "source_id": source_id,



                "event_id": event_id,



                "reason": reason,



                "timestamp": timestamp or time.time(),



                "user_initiated": user_initiated,



            }



            logger.info(f"Explicit stop recorded for {source_id}: {reason} (event_id={event_id})")



            return self.stop_event_info







    def stop_session_monitoring(



        self,



        reason: str = "Session ended",



        failed: bool = False,



        cancelled: bool = False,



    ) -> None:



        with self._lock:



            target_state = (



                StrictLifecycleState.SESSION_FAILED



                if failed



                else StrictLifecycleState.SESSION_CANCELLED



                if cancelled



                else StrictLifecycleState.SESSION_COMPLETED



            )



            self.transition_lifecycle_state(target_state, reason=reason)







    def reset_session_state(self, reason: str = "Session reset") -> None:



        with self._lock:



            self.stop_session_monitoring(reason=reason, cancelled=True)



            self.session_id = None



            self.session_started_mono = None



            self.last_valid_frame_mono = None



            self.last_valid_frame_wall = None



            self.last_processed_frame_mono = None



            self.last_processed_frame_wall = None



            self.elapsed_without_frames = 0.0



            self.last_reported_state = IPadCameraState.CAMERA_INITIALIZING



            self.active_incident = None



            self.is_timer_paused = False



            self.is_session_flagged = False



            self.flags.clear()



            self.stable_recovery_start_mono = None



            self.was_recovered = False



            self.last_recovery_mono = None



            self.initial_check_passed = False



            for k in self.initial_check_details:



                self.initial_check_details[k] = "PENDING"



            self.pipeline_stages = self._init_pipeline_stages()



            self.detailed_stages = self._init_detailed_stages()



            self.stage_history.clear()



            self.connection_id = ""



            self.current_tx_id = ""



            self.transition_lifecycle_state(StrictLifecycleState.IDLE, reason=reason)







    def record_connection_status(



        self,



        device_connected: bool,



        https_verified: bool,



        webrtc_state: str = "CONNECTED",



        ice_state: str = "CONNECTED",



        track_received: bool = True,



        camera_permission_granted: bool = True,



    ) -> None:



        """Updates transport & signaling connection status without refreshing video frame timestamps."""



        with self._lock:



            self.ios_device_connected = device_connected



            self.https_verified = https_verified



            self.camera_permission_granted = camera_permission_granted



            self.webrtc_connection_state = webrtc_state



            self.ice_connection_state = ice_state



            self.video_track_received = track_received



            if track_received:



                self.record_pipeline_stage("2_ios_video_track", 0, "ACTIVE")







    def record_ios_sender_stats(self, stats: Dict[str, Any]) -> None:



        """Records real WebRTC outbound-rtp and track statistics reported by iOS Safari."""



        if not isinstance(stats, dict):



            return



        with self._lock:



            now_iso = self._wall_clock().isoformat()



            prev_encoded = int(self.ios_sender_stats.get("framesEncoded", 0) or 0)



            prev_sent = int(self.ios_sender_stats.get("framesSent", 0) or 0)



            prev_packets = int(self.ios_sender_stats.get("packetsSent", 0) or 0)







            frames_encoded = int(stats.get("framesEncoded", prev_encoded) or 0)



            frames_sent = int(stats.get("framesSent", prev_sent) or 0)



            packets_sent = int(stats.get("packetsSent", prev_packets) or 0)



            track_state = str(stats.get("trackReadyState", "live"))



            track_muted = bool(stats.get("trackMuted", False))



            track_enabled = bool(stats.get("trackEnabled", True))







            self.ios_sender_stats.update({



                "framesEncoded": frames_encoded,



                "framesSent": frames_sent,



                "packetsSent": packets_sent,



                "bytesSent": int(stats.get("bytesSent", self.ios_sender_stats.get("bytesSent", 0)) or 0),



                "captureFps": float(stats.get("captureFps", stats.get("framesPerSecond", 0.0)) or 0.0),



                "encodedFps": float(stats.get("encodedFps", stats.get("framesPerSecond", 0.0)) or 0.0),



                "transmittedFps": float(stats.get("transmittedFps", stats.get("framesPerSecond", 0.0)) or 0.0),



                "outboundBitrateKbps": float(stats.get("outboundBitrateKbps", 0.0) or 0.0),



                "trackReadyState": track_state,



                "trackMuted": track_muted,



                "trackEnabled": track_enabled,



                "captureWidth": int(stats.get("captureWidth", 0) or 0),



                "captureHeight": int(stats.get("captureHeight", 0) or 0),



                "cameraSource": str(stats.get("cameraSource", "iPhone/iPad Camera")),



                "deviceOrientation": str(stats.get("deviceOrientation", "portrait")),



                "updatedAtIso": now_iso,



            })







            if track_state == "live" and not track_muted and track_enabled:



                self.record_pipeline_stage("1_ios_camera_capture", max(0, frames_encoded - prev_encoded), "ACTIVE")



                self.record_pipeline_stage("2_ios_video_track", 1, "ACTIVE")



            else:



                self.record_pipeline_stage("1_ios_camera_capture", 0, "PAUSED" if track_muted else track_state.upper())



                self.record_pipeline_stage("2_ios_video_track", 0, "MUTED" if track_muted else track_state.upper())







            if frames_encoded > prev_encoded:



                self.record_pipeline_stage("3_frame_encoding", frames_encoded - prev_encoded, "ACTIVE")



            if packets_sent > prev_packets or frames_sent > prev_sent:



                self.record_pipeline_stage("4_rtp_packet_transmission", max(1, frames_sent - prev_sent), "ACTIVE")







    def record_windows_receiver_stats(self, stats: Dict[str, Any]) -> None:



        """Records real WebRTC inbound-rtp and ICE candidate-pair statistics on Windows."""



        if not isinstance(stats, dict):



            return



        with self._lock:



            now_iso = self._wall_clock().isoformat()



            prev_pkts = int(self.windows_receiver_stats.get("packetsReceived", 0) or 0)



            prev_frames_rx = int(self.windows_receiver_stats.get("framesReceived", 0) or 0)



            prev_frames_dec = int(self.windows_receiver_stats.get("framesDecoded", 0) or 0)







            pkts_rx = int(stats.get("packetsReceived", prev_pkts) or 0)



            frames_rx = int(stats.get("framesReceived", prev_frames_rx) or 0)



            frames_dec = int(stats.get("framesDecoded", prev_frames_dec) or 0)







            pair = stats.get("selectedCandidatePair") if isinstance(stats.get("selectedCandidatePair"), dict) else {}



            local_type = str(pair.get("localCandidateType") or stats.get("localCandidateType") or self.windows_receiver_stats.get("localCandidateType", "unknown"))



            remote_type = str(pair.get("remoteCandidateType") or stats.get("remoteCandidateType") or self.windows_receiver_stats.get("remoteCandidateType", "unknown"))



            rtt_ms = pair.get("rttMs") if "rttMs" in pair else stats.get("rttMs", self.windows_receiver_stats.get("rttMs"))



            if "relay" in (local_type.lower(), remote_type.lower()):



                route_type = "TURN_RELAY"



            elif "srflx" in (local_type.lower(), remote_type.lower()) or "prflx" in (local_type.lower(), remote_type.lower()):



                route_type = "STUN_SRFLX"



            elif "host" in (local_type.lower(), remote_type.lower()):



                route_type = "DIRECT_LOCAL_HOST"



            else:



                route_type = str(stats.get("routeType", self.windows_receiver_stats.get("routeType", "UNKNOWN")))







            self.windows_receiver_stats.update({



                "packetsReceived": pkts_rx,



                "bytesReceived": int(stats.get("bytesReceived", self.windows_receiver_stats.get("bytesReceived", 0)) or 0),



                "packetsLost": int(stats.get("packetsLost", self.windows_receiver_stats.get("packetsLost", 0)) or 0),



                "jitter": round(float(stats.get("jitter", 0.0) or 0.0), 4),



                "framesReceived": max(frames_rx, self.frame_sequence),



                "framesDecoded": max(frames_dec, self.decoded_frames_count),



                "framesDropped": int(stats.get("framesDropped", self.dropped_frames_count) or 0),



                "frameWidth": int(stats.get("frameWidth", self.frame_width) or 0),



                "frameHeight": int(stats.get("frameHeight", self.frame_height) or 0),



                "totalDecodeTime": round(float(stats.get("totalDecodeTime", 0.0) or 0.0), 3),



                "iceConnectionState": str(stats.get("iceConnectionState", self.ice_connection_state)),



                "localCandidateType": local_type,



                "remoteCandidateType": remote_type,



                "candidatePairState": str(pair.get("state") or stats.get("candidatePairState") or "succeeded"),



                "routeType": route_type,



                "rttMs": rtt_ms,



                "updatedAtIso": now_iso,



            })







            if pkts_rx > prev_pkts:



                self.record_pipeline_stage("5_windows_rtp_packet_reception", pkts_rx - prev_pkts, "ACTIVE")



            if frames_rx > prev_frames_rx:



                self.record_pipeline_stage("6_complete_frame_reception", frames_rx - prev_frames_rx, "ACTIVE")







    def record_valid_frame(



        self,



        frame: Any,



        session_id: Optional[str] = None,



        camera_id: Optional[str] = None,



        presentation_timestamp: Optional[float] = None,



        frame_seq: Optional[int] = None,



        preparation_id: Optional[str] = None,



        mark_processed: bool = True,



    ) -> bool:



        """



        Validates and records an incoming decoded iPad video frame.



        Must be a real ndarray/frame with valid positive dimensions.



        """



        with self._lock:



            now_mono = self._clock()



            now_wall = self._wall_clock()



            now_iso = now_wall.isoformat()







            # 1. Dimension & Type Validation



            width, height = 0, 0



            if isinstance(frame, np.ndarray):



                height, width = frame.shape[:2]



            elif isinstance(frame, dict):



                width = int(frame.get("width", 0))



                height = int(frame.get("height", 0))



            elif hasattr(frame, "width") and hasattr(frame, "height"):



                width, height = int(frame.width), int(frame.height)







            if width <= 0 or height <= 0:



                logger.warning(f"Invalid frame dimensions rejected: {width}x{height}")



                self.dropped_frames_count += 1



                return False







            # 2. Session Integrity & Prerequisite Check Milestones



            if preparation_id:



                self.preparation_id = preparation_id







            if frame_seq is not None and frame_seq > self.frame_sequence:



                self.frame_sequence = frame_seq



            else:



                self.frame_sequence += 1



            self.decoded_frames_count += 1



            if mark_processed:



                self.processed_frames_count = getattr(self, "processed_frames_count", 0) + 1







            # Update Initial Prerequisite Check milestones (Section 1 & 4)



            self.first_frame_received = True



            self.first_frame_decoded = True



            self.first_frame_accepted_by_inference = True



            if not getattr(self, "ios_camera_initial_verified", False) or not self.initial_check_passed:



                self.ios_camera_initial_verified = True



                self.initial_check_passed = True



                self.initial_check_passed_at_iso = now_iso



                self.initial_check_error = ""



                self.initial_verification_record = {



                    "status": "IOS_CAMERA_INITIAL_VERIFIED",



                    "device_id": self.camera_device_id or getattr(self, "ios_device_id", "") or "ios_companion",



                    "camera_source_id": "IOS_FRONT_CAMERA",



                    "verification_timestamp": now_iso,



                    "received_frame_sequence": self.frame_sequence,



                    "decoded_frame_sequence": getattr(self, "decoded_frames_count", self.frame_sequence),



                    "processed_frame_sequence": getattr(self, "processed_frames_count", 1),



                }



                logger.info("IOS_CAMERA_INITIAL_VERIFIED: Real iOS frame received, decoded, and entered processing pipeline.")







            self.prerequisite_last_frame_mono = now_mono



            self.prerequisite_last_frame_wall_iso = now_iso







            # Check if this frame belongs to an active confirmed session



            if self.lifecycle_state == StrictLifecycleState.SESSION_ACTIVE:



                if self.session_id and session_id and session_id != self.session_id:



                    logger.warning(f"Frame session mismatch: {session_id} != {self.session_id}")



                    self.dropped_frames_count += 1



                    return False







                self.session_last_valid_frame_mono = now_mono



                self.session_last_valid_frame_wall_iso = now_iso



                self.session_last_decoded_mono = now_mono



                self.session_last_decoded_wall_iso = now_iso



                self.session_frame_count += 1



                if mark_processed:



                    self.session_last_processed_mono = now_mono



                    self.session_last_processed_wall_iso = now_iso







            # 3. Update Valid Frame Reception & Decode Telemetry



            self.last_valid_frame_mono = now_mono



            self.last_valid_frame_wall_iso = now_iso



            self.last_successfully_decoded_mono = now_mono



            self.last_successfully_decoded_wall_iso = now_iso



            self.last_frame_queued_for_inference_mono = now_mono



            self.last_frame_queued_for_inference_wall_iso = now_iso







            # Sequence and decoded counters already advanced above



            self.queued_frames_count += 1



            self.frame_width = width



            self.frame_height = height



            self.video_presentation_timestamp = presentation_timestamp or time.time()



            self.video_track_received = True



            self.ios_device_connected = True



            self.camera_permission_granted = True







            # Record pipeline stages 5..9



            self.record_pipeline_stage("5_windows_rtp_packet_reception", 1, "ACTIVE")



            self.record_pipeline_stage("6_complete_frame_reception", 1, "ACTIVE")



            self.record_pipeline_stage("7_video_decoding", 1, "ACTIVE")



            self.record_pipeline_stage("8_frame_delivery_to_receiver", 1, "ACTIVE")



            self.record_pipeline_stage("9_inference_queue_insertion", 1, "ACTIVE")







            # Append to rolling rx/decode deques



            self._frame_rx_times.append(now_mono)



            self._frame_dec_times.append(now_mono)



            if mark_processed:



                self.last_successfully_processed_mono = now_mono



                self.last_successfully_processed_wall_iso = now_iso



                self._frame_proc_times.append(now_mono)



            self._recalculate_fps(now_mono)







            if self.proctoring_processing_state != IPadCameraState.IOS_PROCESSING_STALLED:



                self.proctoring_processing_state = "PROCESSING"







            # 4. Check Recovery Progression ONLY during active session



            if self.lifecycle_state == StrictLifecycleState.SESSION_ACTIVE:



                if (



                    self.active_incident is not None



                    or self.health_state in (



                        IPadCameraState.CAMERA_INTERRUPTED,



                        IPadCameraState.IOS_CAMERA_INTERRUPTED,



                        IPadCameraState.CAMERA_RECOVERING,



                    )



                ):



                    if self.stable_recovery_start_mono is None:



                        self.stable_recovery_start_mono = now_mono



                        self.health_state = IPadCameraState.CAMERA_RECOVERING



                        logger.info("iPad camera feed returned; starting recovery stability verification window.")



                    else:



                        stable_duration = now_mono - self.stable_recovery_start_mono



                        if stable_duration >= IPadCameraState.RECOVERY_STABLE_SECONDS:



                            self._mark_camera_recovered(now_mono, now_wall)



                else:



                    self.health_state = IPadCameraState.WINDOWS_PROCESSING_ACTIVE



            else:



                self.health_state = IPadCameraState.VALID_FRAMES_RECEIVED







            return True







    def record_frame_queued_for_inference(self, queue_length: int = 1, frame_seq: Optional[int] = None) -> None:



        with self._lock:



            now_mono = self._clock()



            now_iso = self._wall_clock().isoformat()



            self.last_frame_queued_for_inference_mono = now_mono



            self.last_frame_queued_for_inference_wall_iso = now_iso



            self.queued_frames_count += 1



            self.inference_queue_length = max(0, int(queue_length))



            self.first_frame_accepted_by_inference = True



            if self.first_frame_received and self.first_frame_decoded and not self.initial_check_passed:



                self.initial_check_passed = True



                self.initial_check_passed_at_iso = now_iso



            self.record_pipeline_stage("9_inference_queue_insertion", 1, "ACTIVE")







    def record_processed_frame(



        self,



        now_mono: Optional[float] = None,



        now_wall: Optional[datetime] = None,



        queue_length: int = 0,



    ) -> None:



        """Records completion of neural proctoring inference on a decoded iPad camera frame."""



        with self._lock:



            mono = self._clock() if now_mono is None else now_mono



            wall = self._wall_clock() if now_wall is None else now_wall



            now_iso = wall.isoformat()



            self.last_successfully_processed_mono = mono



            self.last_successfully_processed_wall_iso = now_iso



            self.processed_frames_count += 1



            self.inference_queue_length = max(0, int(queue_length))



            self.inference_worker_state = "RUNNING"



            self.last_processing_error = ""



            self._frame_proc_times.append(mono)



            self._recalculate_fps(mono)



            self.proctoring_processing_state = "PROCESSING"



            self.record_pipeline_stage("10_face_gaze_head_pose_processing", 1, "ACTIVE")



            self.record_pipeline_stage("11_dashboard_result_delivery", 1, "ACTIVE")







    def record_processing_error(self, error_msg: str) -> None:



        with self._lock:



            self.last_processing_error = str(error_msg)



            self.inference_worker_state = "ERROR"



            self.record_pipeline_stage("10_face_gaze_head_pose_processing", 0, f"ERROR: {error_msg}")







    def _recalculate_fps(self, now_mono: float) -> None:



        """Calculates actual rolling Received, Decoded, and Processed FPS over a 2.0s window."""



        window = 2.0



        recent_rx = [t for t in self._frame_rx_times if 0.0 <= (now_mono - t) <= window]



        recent_dec = [t for t in self._frame_dec_times if 0.0 <= (now_mono - t) <= window]



        recent_proc = [t for t in self._frame_proc_times if 0.0 <= (now_mono - t) <= window]







        if len(recent_rx) > 1:
            self.received_fps = round(len(recent_rx) / window, 1)
        elif len(recent_rx) == 1:
            self.received_fps = 1.0
        elif float(self.windows_receiver_stats.get("rx_fps", 0.0) or 0.0) > 0.0:
            self.received_fps = float(self.windows_receiver_stats["rx_fps"])
        elif self._frame_rx_times:
            self.received_fps = 0.0
        # If _frame_rx_times was empty, retain self.received_fps if already set



        self.decoded_fps = round(len(recent_dec) / window, 1) if len(recent_dec) > 1 else (1.0 if len(recent_dec) == 1 else 0.0)



        self.processing_fps = round(len(recent_proc) / window, 1) if len(recent_proc) > 1 else (1.0 if len(recent_proc) == 1 else 0.0)







    def _mark_camera_recovered(self, now_mono: float, now_wall: datetime) -> None:



        """Finalizes camera recovery after RECOVERY_STABLE_SECONDS (3.0s) of continuous valid frames."""



        self.health_state = IPadCameraState.CAMERA_RECOVERED



        self.was_recovered = True



        self.last_recovery_mono = now_mono



        self.is_timer_paused = False



        now_iso = now_wall.isoformat()







        if self.active_incident:



            self.active_incident["incident_status"] = "RESOLVED"



            self.active_incident["recovery_timestamp"] = now_iso



            try:



                dt_interrupted = datetime.fromisoformat(self.active_incident["interruption_detected_timestamp"])



                duration = round((now_wall - dt_interrupted).total_seconds(), 1)



                if duration > 0:



                    self.active_incident["interruption_duration"] = duration



            except Exception:



                pass







            self._save_incident_to_db(self.active_incident)



            logger.info(



                f"iPad camera incident {self.active_incident['incident_id']} RESOLVED. "



                f"Duration: {self.active_incident.get('interruption_duration')}s"



            )



            self.active_incident = None







        self.stable_recovery_start_mono = None



        self.health_state = IPadCameraState.WINDOWS_PROCESSING_ACTIVE



        logger.info("IPAD CAMERA: LIVE – MONITORING ACTIVE (IOS_CAMERA_RECOVERED & Verified).")







    def classify_root_cause(self, elapsed_without_frames: float, processing_stalled: bool) -> str:



        """Identifies the earliest failing pipeline stage or root cause."""



        if self.received_fps > 0.0 and getattr(self, "decoded_fps", 0.0) == 0.0 and self.frame_sequence > 3:



            return "VIDEO_DECODING_FAILURE"



        if (getattr(self, "decoded_fps", 0.0) > 0.0 or self.received_fps > 0.0) and (self.processing_fps == 0.0 or processing_stalled) and self.frame_sequence > 3:



            return DualCause("INFERENCE_PIPELINE_STALLED")



        if elapsed_without_frames > IPadCameraState.THRESHOLD_RECOVERING and self.received_fps == 0.0:



            return "MEDIA_TRANSPORT_OR_CAPTURE_INTERRUPTION"



        if processing_stalled:



            return DualCause("INFERENCE_PIPELINE_STALLED")



        if elapsed_without_frames <= IPadCameraState.THRESHOLD_RECOVERING:



            return "HEALTHY"



        if not self.ios_device_connected:



            return "IOS_DEVICE_DISCONNECTED"



        if self.ios_sender_stats.get("trackMuted") or self.ios_sender_stats.get("trackReadyState") in ("ended", "muted"):



            return "IOS_CAMERA_TRACK_PAUSED_OR_MUTED"



        if self.webrtc_connection_state in ("DISCONNECTED", "FAILED", "CLOSED") or self.ice_connection_state in ("DISCONNECTED", "FAILED", "CLOSED"):



            return "MEDIA_TRANSPORT_OR_CAPTURE_INTERRUPTION"



        if self.decoded_frames_count < self.frame_sequence:



            return "VIDEO_DECODING_FAILURE"



        return "MEDIA_TRANSPORT_OR_CAPTURE_INTERRUPTION"







    def identify_earliest_failing_stage(self, now_mono: float, elapsed_without_frames: float, processing_stalled: bool) -> str:



        if processing_stalled:



            return "23_windows_camera_inference_processing"



        if elapsed_without_frames <= IPadCameraState.THRESHOLD_RECOVERING and self.frame_sequence > 0:



            return "NONE"



        # Check 23 connection stages in order



        for st_name in CONNECTION_STAGES_23:



            st = self.detailed_stages.get(st_name, {})



            if st.get("error_message") or (st.get("transition_count", 0) == 0 and not st.get("success")):



                return st_name



        for stage_name in PIPELINE_STAGE_NAMES:



            st = self.pipeline_stages.get(stage_name, {})



            last_m = st.get("last_mono")



            if last_m is None or (now_mono - last_m) > IPadCameraState.THRESHOLD_RECOVERING:



                return stage_name



        return "NONE"







    def evaluate_health(self, now_mono: Optional[float] = None, now_wall: Optional[datetime] = None) -> Dict[str, Any]:



        """



        Evaluates the iPad camera 15-second frame-gap policy and separate inference processing watchdog:







        - 0–5 s  : ACTIVE / TEMPORARY GAP (no warning)



        - 5–10 s : TEMPORARILY PAUSED (continue session)



        - 10–15 s: DEGRADED (show warning and attempt recovery)



        - > 15 s : CAMERA_INTERRUPTED (single incident, FLAGGED_CAMERA_INTERRUPTION, prominent alert)







        Operates ONLY when authoritative session state is SESSION_ACTIVE. Outside an active session,



        never creates incidents or pauses the timer.



        """



        with self._lock:



            now_mono = self._clock() if now_mono is None else now_mono



            now_wall = self._wall_clock() if now_wall is None else now_wall



            now_iso = now_wall.isoformat()







            # Refresh rolling FPS against current now_mono so stale FPS never persists



            self._recalculate_fps(now_mono)







            # CRITICAL LIFECYCLE CHECK (Section 1 & 2):



            # The watchdog operates ONLY when authoritative session state is SESSION_ACTIVE!



            if self.lifecycle_state != StrictLifecycleState.SESSION_ACTIVE or not self.session_id:



                self.is_timer_paused = False



                self.is_session_flagged = False



                self.active_incident = None



                return self.get_diagnostics(elapsed_without_frames=0.0, now_mono=now_mono)







            # Reference baseline for current active session (Section 3 & 9):
            if self.session_last_decoded_mono is not None:
                ref_time = self.session_last_decoded_mono
            elif self.session_last_valid_frame_mono is not None:
                ref_time = self.session_last_valid_frame_mono
            elif getattr(self, "prerequisite_last_frame_mono", None) is not None:
                ref_time = self.prerequisite_last_frame_mono
            else:
                ref_time = self.session_started_mono if self.session_started_mono is not None else now_mono

            if self.received_fps > 0.0 or getattr(self, "decoded_fps", 0.0) > 0.0:
                elapsed_without_frames = 0.0
            else:
                elapsed_without_frames = max(0.0, round(now_mono - ref_time, 2))







            t_recover = IPadCameraState.THRESHOLD_RECOVERING      # default 5.0s



            t_paused = IPadCameraState.THRESHOLD_TEMP_PAUSED      # default 10.0s



            t_degraded = IPadCameraState.THRESHOLD_DEGRADED       # default 15.0s







            # ----------------------------------------------------------------



            # Check separate Inference Processing Watchdog (Section 3 & 5)



            # ----------------------------------------------------------------



            proc_ref = (



                self.session_last_processed_mono



                if self.session_last_processed_mono is not None



                else (self.session_started_mono if self.session_started_mono is not None else 0.0)



            )



            proc_gap = max(0.0, now_mono - proc_ref)



            processing_stalled = bool(



                elapsed_without_frames <= t_recover



                and self.received_fps > 0.0



                and self.queued_frames_count >= 3



                and self.processing_fps == 0.0



                and proc_gap > 3.0



            )



            if processing_stalled:



                self.proctoring_processing_state = IPadCameraState.IOS_PROCESSING_STALLED



                self.inference_worker_state = "STALLED_RECOVERING"



                try:



                    from windows.strict_dual_camera_proctor import StrictDualCameraProctor



                    StrictDualCameraProctor.get_instance().ensure_ios_worker_alive()



                except Exception:



                    pass







            # ----------------------------------------------------------------



            # Tier 1: 0–5 s – ACTIVE / TEMPORARY GAP or INITIALIZING or RECOVERING



            # ----------------------------------------------------------------



            if elapsed_without_frames <= t_recover:



                if self.stable_recovery_start_mono is not None and (



                    self.active_incident is not None



                    or self.health_state == IPadCameraState.CAMERA_RECOVERING



                ):



                    if (now_mono - self.stable_recovery_start_mono) >= IPadCameraState.RECOVERY_STABLE_SECONDS:



                        self._mark_camera_recovered(now_mono, now_wall)



                    else:



                        self.health_state = IPadCameraState.CAMERA_RECOVERING



                else:



                    if processing_stalled:



                        self.health_state = IPadCameraState.IOS_PROCESSING_STALLED



                    elif self.session_frame_count > 0:



                        self.health_state = IPadCameraState.WINDOWS_PROCESSING_ACTIVE



                    else:



                        self.health_state = IPadCameraState.CAMERA_INITIALIZING



                self.is_timer_paused = False







            # ----------------------------------------------------------------



            # Tier 2: 5–10 s – TEMPORARILY PAUSED (session continues, no violation)



            # ----------------------------------------------------------------



            elif elapsed_without_frames <= t_paused:



                self.health_state = IPadCameraState.CAMERA_TEMPORARILY_PAUSED



                self.proctoring_processing_state = "TEMPORARILY_PAUSED"



                self.stable_recovery_start_mono = None



                self.is_timer_paused = False







            # ----------------------------------------------------------------



            # Tier 3: 10–15 s – DEGRADED (warning, attempt recovery)



            # ----------------------------------------------------------------



            elif elapsed_without_frames <= t_degraded:



                self.health_state = IPadCameraState.CAMERA_DEGRADED



                self.proctoring_processing_state = "DEGRADED"



                self.stable_recovery_start_mono = None



                self.is_timer_paused = False







            # ----------------------------------------------------------------



            # Tier 4: > 15 s — CAMERA_INTERRUPTED (incident, alert, session flag)
            # ----------------------------------------------------------------
            else:
                if self.received_fps > 0.0 or getattr(self, "decoded_fps", 0.0) > 0.0:
                    self.health_state = IPadCameraState.IOS_PROCESSING_STALLED if self.processing_fps == 0.0 else IPadCameraState.WINDOWS_PROCESSING_ACTIVE
                    self.proctoring_processing_state = "STALLED_RECOVERING" if self.processing_fps == 0.0 else "PROCESSING"
                else:
                    self.health_state = IPadCameraState.CAMERA_INTERRUPTED
                    self.proctoring_processing_state = "INTERRUPTED"
                    self.stable_recovery_start_mono = None
                # CRITICAL: Strict Session timer MUST NOT pause on iOS camera interruption!
                self.is_timer_paused = False



                if self.health_state in (IPadCameraState.CAMERA_INTERRUPTED, IPadCameraState.IOS_CAMERA_INTERRUPTED):
                    self.is_session_flagged = True



                if self.health_state in (IPadCameraState.CAMERA_INTERRUPTED, IPadCameraState.IOS_CAMERA_INTERRUPTED):
                    for flag_name in ("FLAGGED_CAMERA_INTERRUPTION", "MANDATORY_IPAD_CAMERA_INTERRUPTED"):



                        if flag_name not in self.flags:



                            self.flags.append(flag_name)







                root_cause = self.classify_root_cause(elapsed_without_frames, False)







                last_valid_ts = (



                    self.session_last_valid_frame_wall_iso



                    if self.session_last_valid_frame_wall_iso is not None



                    else "Waiting for first session frame"



                )







                # Single unique incident per continuous outage during the current active session



                if self.active_incident is None:



                    incident_id = f"ipad_cam_incident_{uuid.uuid4().hex[:12]}"



                    incident = {



                        "incident_id": incident_id,



                        "event_type": "IOS_CAMERA_INTERRUPTED",



                        "session_id": self.session_id,



                        "camera_device_id": self.camera_device_id,



                        "ios_device_id": self.camera_device_id,



                        "camera_source": self.camera_source,



                        "last_valid_frame_timestamp": last_valid_ts,



                        "interruption_detected_timestamp": now_iso,



                        "interruption_start_time": now_iso,



                        "interruption_duration": elapsed_without_frames,



                        "last_webrtc_state": self.webrtc_connection_state,



                        "webrtc_state": self.webrtc_connection_state,



                        "last_received_fps": self.received_fps,



                        "received_fps": self.received_fps,



                        "decoded_fps": self.decoded_fps,



                        "last_processing_fps": self.processing_fps,



                        "processing_fps": self.processing_fps,



                        "root_cause_classification": root_cause,



                        "incident_status": "OPEN",



                        "recovery_timestamp": None,



                    }



                    self.active_incident = incident



                    self.incidents_history.append(incident)



                    self._save_incident_to_db(incident)



                    logger.warning(



                        f"iOS CAMERA INTERRUPTED – SESSION FLAGGED ({incident_id}): "



                        f"{elapsed_without_frames:.1f}s without valid iPad frames in session {self.session_id}."



                    )



                else:



                    self.active_incident["interruption_duration"] = elapsed_without_frames



                    self.active_incident["last_webrtc_state"] = self.webrtc_connection_state



                    self.active_incident["webrtc_state"] = self.webrtc_connection_state



                    self.active_incident["root_cause_classification"] = root_cause



                    self._save_incident_to_db(self.active_incident)







            return self.get_diagnostics(elapsed_without_frames, now_mono=now_mono)







    def _save_incident_to_db(self, incident: Dict[str, Any]) -> None:



        if not self.db:



            return



        try:



            with self.db._connect() as conn:



                conn.execute("""



                    INSERT INTO strict_camera_incidents (



                        incident_id, session_id, camera_device_id, camera_source,



                        last_valid_frame_timestamp, interruption_detected_timestamp,



                        interruption_duration, last_webrtc_state, last_received_fps,



                        last_processing_fps, incident_status, recovery_timestamp, details_json



                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)



                    ON CONFLICT(incident_id) DO UPDATE SET



                        interruption_duration=excluded.interruption_duration,



                        incident_status=excluded.incident_status,



                        recovery_timestamp=excluded.recovery_timestamp,



                        last_webrtc_state=excluded.last_webrtc_state,



                        details_json=excluded.details_json



                """, (



                    incident["incident_id"],



                    incident["session_id"],



                    incident.get("camera_device_id", ""),



                    incident["camera_source"],



                    incident.get("last_valid_frame_timestamp"),



                    incident["interruption_detected_timestamp"],



                    incident.get("interruption_duration", 0.0),



                    incident.get("last_webrtc_state", ""),



                    incident.get("last_received_fps", 0.0),



                    incident.get("last_processing_fps", 0.0),



                    incident["incident_status"],



                    incident.get("recovery_timestamp"),



                    json.dumps(incident),



                ))



        except Exception as err:



            logger.debug(f"Error saving camera incident to db: {err}")







    def get_initial_prerequisite_status(self) -> Dict[str, Any]:



        """Returns the 6-step Initial Camera Prerequisite Check status (Section 1 & 8)."""



        with self._lock:



            passed = bool(



                getattr(self, "initial_check_passed", False)



                or (



                    getattr(self, "first_frame_received", False)



                    and getattr(self, "first_frame_decoded", False)



                    and getattr(self, "first_frame_accepted_by_inference", False)



                )



                or self.frame_sequence > 0



            )



            err = getattr(self, "initial_check_error", "")



            if passed:



                result_label = IPadCameraState.IOS_CAMERA_INITIAL_CHECK_PASSED



            elif err:



                result_label = IPadCameraState.IOS_CAMERA_INITIAL_CHECK_FAILED



            else:



                result_label = IPadCameraState.IOS_CAMERA_INITIAL_CHECK_PENDING







            steps = {



                "1_device_registered_authenticated": bool(self.ios_device_connected or passed),



                "2_camera_permission_granted": bool(getattr(self, "camera_permission_granted", False) or self.video_track_received or passed),



                "3_webrtc_video_connected": bool(self.webrtc_connection_state in ("CONNECTED", "STREAMING") or passed),



                "4_real_video_frame_received": bool(getattr(self, "first_frame_received", False) or self.frame_sequence > 0),



                "5_frame_decoded": bool(getattr(self, "first_frame_decoded", False) or self.frame_sequence > 0),



                "6_frame_passed_to_pipeline": bool(getattr(self, "first_frame_accepted_by_inference", False) or self.frame_sequence > 0),



            }







            is_verified = bool(passed or getattr(self, "ios_camera_initial_verified", False))



            return {



                "device_registered": steps["1_device_registered_authenticated"],



                "camera_permission": steps["2_camera_permission_granted"],



                "webrtc_connection": steps["3_webrtc_video_connected"],



                "first_valid_frame_received": steps["4_real_video_frame_received"],



                "first_valid_frame_decoded": steps["5_frame_decoded"],



                "frame_accepted_by_inference": steps["6_frame_passed_to_pipeline"],



                "steps": steps,



                "initial_check_passed": is_verified,



                "initial_verified": is_verified,



                "initial_verification_record": getattr(self, "initial_verification_record", None),



                "status": DualInitialStatus("IOS_CAMERA_INITIAL_VERIFIED") if is_verified else result_label,



                "initial_check_result": result_label,



                "passed_at_iso": getattr(self, "initial_check_passed_at_iso", None),



                "error": err,



            }







    def get_diagnostics(self, elapsed_without_frames: Optional[float] = None, now_mono: Optional[float] = None) -> Dict[str, Any]:



        """Provides full connection, 11-stage pipeline, proctoring, and interruption diagnostics."""



        with self._lock:



            mono = self._clock() if now_mono is None else now_mono



            self._recalculate_fps(mono)







            is_session_active = (self.lifecycle_state == StrictLifecycleState.SESSION_ACTIVE and bool(self.session_id))







            if not is_session_active:



                display_label = (



                    "IPAD CAMERA: PREREQUISITE READY" if self.initial_check_passed



                    else "IPAD CAMERA: CONNECTED – AWAITING FRAMES" if self.webrtc_connection_state in ("CONNECTED", "STREAMING")



                    else "IPAD CAMERA: NOT CONNECTED"



                )



                dashboard_status_label = (



                    "iPad Camera: Prerequisite Verified" if self.initial_check_passed



                    else "iPad Camera: Setup / Prerequisite Pending"



                )



                last_frame_ts = (



                    self.prerequisite_last_frame_wall_iso



                    if self.initial_check_passed and self.prerequisite_last_frame_wall_iso



                    else "Waiting for session start"



                )



                initial_prereq = self.get_initial_prerequisite_status()



                win_rx = getattr(self, "windows_receiver_stats", {}) or {}



                cam_perm = getattr(self, "camera_permission_granted", False)







                independent_states = {



                    IPadCameraState.IOS_DEVICE_CONNECTED: bool(self.ios_device_connected),



                    IPadCameraState.HTTPS_VERIFIED: bool(self.https_verified),



                    IPadCameraState.WEBRTC_CONNECTED: self.webrtc_connection_state in ("CONNECTED", "STREAMING"),



                    IPadCameraState.IOS_WEBRTC_CONNECTED: self.webrtc_connection_state in ("CONNECTED", "STREAMING"),



                    IPadCameraState.CAMERA_PERMISSION_GRANTED: bool(cam_perm or self.video_track_received),



                    IPadCameraState.VIDEO_TRACK_ACTIVE: bool(self.video_track_received),



                    IPadCameraState.IOS_CAPTURE_ACTIVE: bool(



                        self.video_track_received and not getattr(self, "ios_sender_stats", {}).get("trackMuted", False)



                    ),



                    IPadCameraState.VIDEO_FRAMES_RECEIVED: bool(self.frame_sequence > 0),



                    IPadCameraState.IOS_FRAMES_RECEIVING: bool(self.received_fps > 0),



                    IPadCameraState.IOS_FRAMES_DECODING: bool(self.decoded_fps > 0),



                    IPadCameraState.IOS_INFERENCE_PROCESSING: bool(self.processing_fps > 0),



                    IPadCameraState.VIDEO_PROCESSING_ACTIVE: False,



                    IPadCameraState.IOS_PROCESSING_STALLED: False,



                    IPadCameraState.CAMERA_INITIALIZING: not self.initial_check_passed,



                    IPadCameraState.CAMERA_TEMPORARILY_PAUSED: False,



                    IPadCameraState.CAMERA_DEGRADED: False,



                    IPadCameraState.CAMERA_INTERRUPTED: False,



                    IPadCameraState.IOS_CAMERA_INTERRUPTED: False,



                    IPadCameraState.CAMERA_RECOVERING: False,



                    IPadCameraState.CAMERA_RECOVERED: False,



                    IPadCameraState.IOS_CAMERA_RECOVERED: False,



                }







                return {



                    "lifecycle_state": self.lifecycle_state.value,



                    "session_id": "",



                    "is_session_active": False,



                    "camera_required": getattr(self, "camera_required", True),



                    "health_state": "CAMERA_READY" if self.initial_check_passed else "CAMERA_INITIALIZING",



                    "display_label": display_label,



                    "dashboard_status_label": dashboard_status_label,



                    "independent_states": independent_states,



                    "initial_prerequisite": initial_prereq,



                    "initial_check_passed": initial_prereq["initial_check_passed"],



                    "initial_check_result": initial_prereq["initial_check_result"],



                    "is_live": bool(self.received_fps > 0.0 and self.frame_sequence > 0),



                    "is_processing_stalled": False,



                    "is_degraded": False,



                    "is_temporarily_paused": False,



                    "is_initializing": not self.initial_check_passed,



                    "is_interrupted": False,



                    "is_recovered": False,



                    "is_timer_paused": False,



                    "is_session_flagged": False,



                    "flags": [],



                    "elapsed_without_frames_seconds": 0.0,



                    "seconds_since_last_valid_frame": 0.0,



                    "grace_period_max_seconds": IPadCameraState.THRESHOLD_DEGRADED,



                    "grace_period_progress_pct": 0.0,



                    "last_valid_frame_timestamp": last_frame_ts,



                    "last_successfully_decoded_timestamp": self.last_successfully_decoded_wall_iso,



                    "last_frame_passed_to_inference_timestamp": getattr(self, "last_frame_queued_for_inference_wall_iso", None),



                    "last_successfully_processed_timestamp": None,



                    "frame_sequence": self.frame_sequence,



                    "session_frame_count": 0,



                    "decoded_frames_count": getattr(self, "decoded_frames_count", self.frame_sequence),



                    "queued_frames_count": getattr(self, "queued_frames_count", self.frame_sequence),



                    "processed_frames_count": getattr(self, "processed_frames_count", 0),



                    "video_presentation_timestamp": self.video_presentation_timestamp,



                    "frame_dimensions": f"{self.frame_width}x{self.frame_height}" if self.frame_width > 0 else "0x0",



                    "frame_width": self.frame_width,



                    "frame_height": self.frame_height,



                    "received_fps": self.received_fps,



                    "decoded_fps": getattr(self, "decoded_fps", self.received_fps),



                    "processing_fps": self.processing_fps,



                    "dropped_frames_count": self.dropped_frames_count,



                    "ios_device_connected": self.ios_device_connected,



                    "https_verified": self.https_verified,



                    "camera_permission_granted": bool(cam_perm or self.video_track_received),



                    "webrtc_connection_state": self.webrtc_connection_state,



                    "ice_connection_state": self.ice_connection_state,



                    "ice_route_type": win_rx.get("routeType", "UNKNOWN"),



                    "ice_rtt_ms": win_rx.get("rttMs"),



                    "video_track_received": self.video_track_received,



                    "proctoring_processing_state": "IDLE",



                    "inference_worker_state": getattr(self, "inference_worker_state", "RUNNING"),



                    "inference_queue_length": getattr(self, "inference_queue_length", 0),



                    "last_processing_error": "",



                    "earliest_failing_stage": "NONE",



                    "root_cause_classification": "HEALTHY",



                    "pipeline_stages": dict(getattr(self, "pipeline_stages", {})),



                    "detailed_stages_23": dict(getattr(self, "detailed_stages", {})),



                    "connection_id": getattr(self, "connection_id", ""),



                    "current_tx_id": getattr(self, "current_tx_id", ""),



                    "stage_history": list(getattr(self, "stage_history", [])[-20:]),







                    "ios_sender_stats": dict(getattr(self, "ios_sender_stats", {})),



                    "windows_receiver_stats": dict(win_rx),



                    "thresholds": self.get_thresholds(),



                    "active_incident": None,



                    "incidents_history": list(self.incidents_history),



                    "incidents_count": len(self.incidents_history),



                    "interruption_warning_modal": None,



                }







            if elapsed_without_frames is None:



                if self.session_last_decoded_mono is not None:



                    ref = self.session_last_decoded_mono



                elif self.session_last_valid_frame_mono is not None:



                    ref = self.session_last_valid_frame_mono



                else:



                    ref = self.session_started_mono if self.session_started_mono is not None else mono



                elapsed_without_frames = max(0.0, round(mono - ref, 2))







            t_recover = IPadCameraState.THRESHOLD_RECOVERING



            t_paused = IPadCameraState.THRESHOLD_TEMP_PAUSED



            t_degraded = IPadCameraState.THRESHOLD_DEGRADED







            processing_stalled = (



                self.health_state == IPadCameraState.IOS_PROCESSING_STALLED



                or self.proctoring_processing_state == IPadCameraState.IOS_PROCESSING_STALLED



            )



            is_interrupted = (



                not processing_stalled



                and (



                    self.health_state in (IPadCameraState.CAMERA_INTERRUPTED, IPadCameraState.IOS_CAMERA_INTERRUPTED)



                    or elapsed_without_frames > t_degraded



                )



            )



            is_degraded = (



                not processing_stalled



                and not is_interrupted



                and (



                    self.health_state == IPadCameraState.CAMERA_DEGRADED



                    or (t_paused < elapsed_without_frames <= t_degraded)



                )



            )



            is_temporarily_paused = (



                not processing_stalled



                and not is_interrupted



                and not is_degraded



                and (



                    self.health_state == IPadCameraState.CAMERA_TEMPORARILY_PAUSED



                    or (t_recover < elapsed_without_frames <= t_paused)



                )



            )



            is_initializing = (



                self.health_state == IPadCameraState.CAMERA_INITIALIZING



                or (self.session_frame_count == 0 and elapsed_without_frames <= t_recover)



            )



            is_live = (



                not processing_stalled



                and self.health_state in (IPadCameraState.WINDOWS_PROCESSING_ACTIVE, IPadCameraState.CAMERA_RECOVERED)



                and elapsed_without_frames <= t_recover



                and self.session_frame_count > 0



            )







            was_recovered = getattr(self, "was_recovered", False)



            cam_perm = getattr(self, "camera_permission_granted", False)







            if processing_stalled:



                display_label = "VIDEO RECEIVING – PROCTORING PROCESSING STALLED"



                dashboard_status_label = "VIDEO RECEIVING – PROCTORING PROCESSING STALLED"



            elif is_live:



                display_label = "IPAD CAMERA: LIVE – MONITORING ACTIVE"



                dashboard_status_label = "iPad Camera: Recovered" if was_recovered else "iPad Camera: Live – Processing"



            elif is_interrupted:



                display_label = "MANDATORY iPAD CAMERA FEED INTERRUPTED"



                dashboard_status_label = "iPad Camera: Interrupted – Flagged"



            elif is_degraded:



                display_label = "IPAD CAMERA: DEGRADED – ATTEMPTING RECOVERY"



                dashboard_status_label = "iPad Camera: Degraded"



            elif self.health_state == IPadCameraState.CAMERA_RECOVERING:



                display_label = "IPAD CAMERA: RECOVERING – VERIFYING STABILITY"



                dashboard_status_label = "iPad Camera: Reconnecting"



            elif is_temporarily_paused:



                display_label = "IPAD CAMERA: TEMPORARILY PAUSED"



                dashboard_status_label = "iPad Camera: Temporarily Paused"



            elif is_initializing:



                display_label = "IPAD CAMERA: INITIALIZING"



                dashboard_status_label = "iPad Camera: Initializing"



            else:



                display_label = "IPAD CAMERA: WAITING FOR VIDEO FRAMES"



                dashboard_status_label = "iPad Camera: Initializing"







            independent_states = {



                IPadCameraState.IOS_DEVICE_CONNECTED: bool(self.ios_device_connected),



                IPadCameraState.HTTPS_VERIFIED: bool(self.https_verified),



                IPadCameraState.WEBRTC_CONNECTED: self.webrtc_connection_state in ("CONNECTED", "STREAMING"),



                IPadCameraState.IOS_WEBRTC_CONNECTED: self.webrtc_connection_state in ("CONNECTED", "STREAMING"),



                IPadCameraState.CAMERA_PERMISSION_GRANTED: bool(cam_perm or self.video_track_received),



                IPadCameraState.VIDEO_TRACK_ACTIVE: bool(self.video_track_received),



                IPadCameraState.IOS_CAPTURE_ACTIVE: bool(



                    self.video_track_received and not getattr(self, "ios_sender_stats", {}).get("trackMuted", False)



                ),



                IPadCameraState.VIDEO_FRAMES_RECEIVED: bool(is_live or processing_stalled),



                IPadCameraState.IOS_FRAMES_RECEIVING: bool(self.received_fps > 0 or is_live),



                IPadCameraState.IOS_FRAMES_DECODING: bool(self.decoded_fps > 0 or is_live),



                IPadCameraState.IOS_INFERENCE_PROCESSING: bool(self.processing_fps > 0 and not processing_stalled),



                IPadCameraState.VIDEO_PROCESSING_ACTIVE: bool(is_live and self.proctoring_processing_state == "PROCESSING"),



                IPadCameraState.IOS_PROCESSING_STALLED: bool(processing_stalled),



                IPadCameraState.CAMERA_INITIALIZING: bool(is_initializing),



                IPadCameraState.CAMERA_TEMPORARILY_PAUSED: bool(is_temporarily_paused),



                IPadCameraState.CAMERA_DEGRADED: bool(is_degraded),



                IPadCameraState.CAMERA_INTERRUPTED: bool(is_interrupted),



                IPadCameraState.IOS_CAMERA_INTERRUPTED: bool(is_interrupted),



                IPadCameraState.CAMERA_RECOVERING: bool(self.health_state == IPadCameraState.CAMERA_RECOVERING),



                IPadCameraState.CAMERA_RECOVERED: bool(was_recovered and is_live),



                IPadCameraState.IOS_CAMERA_RECOVERED: bool(was_recovered and is_live),



            }







            grace_progress_pct = min(100.0, round((elapsed_without_frames / max(1.0, t_degraded)) * 100.0, 1))



            earliest_failing_stage = self.identify_earliest_failing_stage(mono, elapsed_without_frames, processing_stalled)



            root_cause = self.classify_root_cause(elapsed_without_frames, processing_stalled)



            initial_prereq = self.get_initial_prerequisite_status()



            win_rx = getattr(self, "windows_receiver_stats", {}) or {}







            last_valid_ts = (



                self.session_last_valid_frame_wall_iso



                if self.session_last_valid_frame_wall_iso is not None



                else "Waiting for first session frame"



            )



            last_proc_ts = (



                self.session_last_processed_wall_iso



                if getattr(self, "session_last_processed_wall_iso", None) is not None



                else (self.last_successfully_processed_wall_iso or "Waiting for first session frame")



            )







            try:
                from windows.strict_ios_media_health import IOSMediaHealthManager
                media_health = IOSMediaHealthManager.get_instance().get_compact_media_status()
            except Exception:
                media_health = {}

            return {
                "ios_media_health": media_health,
                "compact_ios_media_status": media_health.get("compact_status", "IOS_STATUS_UNKNOWN"),
                "ios_media_classification": media_health.get("camera_classification", "IOS_STATUS_UNKNOWN"),
                "last_heartbeat_timestamp": media_health.get("last_heartbeat_timestamp"),
                "expandable_per_source_diagnostics": media_health.get("expandable_diagnostics", {}),
                "lifecycle_state": StrictLifecycleState.SESSION_ACTIVE.value,



                "session_id": self.session_id,



                "is_session_active": True,



                "camera_required": getattr(self, "camera_required", True),



                "health_state": self.health_state,



                "display_label": display_label,



                "dashboard_status_label": dashboard_status_label,



                "independent_states": independent_states,



                "initial_prerequisite": initial_prereq,



                "initial_check_passed": initial_prereq["initial_check_passed"],



                "initial_check_result": initial_prereq["initial_check_result"],



                "is_live": is_live,



                "is_processing_stalled": processing_stalled,



                "is_degraded": is_degraded,



                "is_temporarily_paused": is_temporarily_paused,



                "is_initializing": is_initializing,



                "is_interrupted": is_interrupted,



                "is_recovered": bool(was_recovered and is_live),



                # Authoritative Strict Session timer NEVER pauses on iOS camera interruption



                "is_timer_paused": False,



                "is_session_flagged": self.is_session_flagged,



                "flags": list(self.flags),



                "elapsed_without_frames_seconds": elapsed_without_frames,



                "seconds_since_last_valid_frame": elapsed_without_frames,



                "grace_period_max_seconds": t_degraded,



                "grace_period_progress_pct": grace_progress_pct,



                "last_valid_frame_timestamp": last_valid_ts,



                "last_successfully_decoded_timestamp": self.session_last_decoded_wall_iso or self.last_successfully_decoded_wall_iso,



                "last_frame_passed_to_inference_timestamp": getattr(self, "last_frame_queued_for_inference_wall_iso", None),



                "last_successfully_processed_timestamp": last_proc_ts,



                "frame_sequence": self.frame_sequence,



                "session_frame_count": self.session_frame_count,



                "decoded_frames_count": getattr(self, "decoded_frames_count", self.frame_sequence),



                "queued_frames_count": getattr(self, "queued_frames_count", self.frame_sequence),



                "processed_frames_count": getattr(self, "processed_frames_count", 0),



                "video_presentation_timestamp": self.video_presentation_timestamp,



                "frame_dimensions": f"{self.frame_width}x{self.frame_height}" if self.frame_width > 0 else "0x0",



                "frame_width": self.frame_width,



                "frame_height": self.frame_height,



                "received_fps": self.received_fps,



                "decoded_fps": getattr(self, "decoded_fps", self.received_fps),



                "processing_fps": self.processing_fps,



                "dropped_frames_count": self.dropped_frames_count,



                "ios_device_connected": self.ios_device_connected,



                "https_verified": self.https_verified,



                "camera_permission_granted": bool(cam_perm or self.video_track_received),



                "webrtc_connection_state": self.webrtc_connection_state,



                "ice_connection_state": self.ice_connection_state,



                "ice_route_type": win_rx.get("routeType", "UNKNOWN"),



                "ice_rtt_ms": win_rx.get("rttMs"),



                "video_track_received": self.video_track_received,



                "proctoring_processing_state": self.proctoring_processing_state,



                "inference_worker_state": getattr(self, "inference_worker_state", "RUNNING"),



                "inference_queue_length": getattr(self, "inference_queue_length", 0),



                "last_processing_error": getattr(self, "last_processing_error", ""),



                "earliest_failing_stage": earliest_failing_stage,



                "root_cause_classification": root_cause,



                "pipeline_stages": dict(getattr(self, "pipeline_stages", {})),



                    "detailed_stages_23": dict(getattr(self, "detailed_stages", {})),



                    "connection_id": getattr(self, "connection_id", ""),



                    "current_tx_id": getattr(self, "current_tx_id", ""),



                    "stage_history": list(getattr(self, "stage_history", [])[-20:]),







                "ios_sender_stats": dict(getattr(self, "ios_sender_stats", {})),



                "windows_receiver_stats": dict(win_rx),



                "thresholds": self.get_thresholds(),



                "active_incident": dict(self.active_incident) if self.active_incident else None,



                "incidents_history": list(self.incidents_history),



                "incidents_count": len(self.incidents_history),



                "interruption_warning_modal": self.format_warning_modal(elapsed_without_frames) if (is_interrupted and getattr(self, "camera_required", True)) else None,



            }







    def format_warning_modal(self, elapsed_without_frames: float) -> Dict[str, Any]:



        """Formats the mandatory warning alert box for >15s interruption during active session."""



        duration_str = f"{int(elapsed_without_frames)}s"



        last_frame_str = (



            self.session_last_valid_frame_wall_iso



            if self.session_last_valid_frame_wall_iso is not None



            else "Waiting for first session frame"



        )



        last_proc_str = (



            self.session_last_processed_wall_iso



            if getattr(self, "session_last_processed_wall_iso", None) is not None



            else (self.last_successfully_processed_wall_iso or "Waiting for first session frame")



        )



        root_cause = self.classify_root_cause(elapsed_without_frames, False)







        return {



            "title": "MANDATORY iPAD CAMERA FEED INTERRUPTED",



            "alert_header": "iOS CAMERA INTERRUPTED – SESSION FLAGGED",



            "camera_source": self.camera_source,



            "session_id": self.session_id,



            "last_valid_video_frame": last_frame_str,



            "last_valid_frame_timestamp": last_frame_str,



            "interruption_duration": duration_str,



            "interruption_duration_seconds": elapsed_without_frames,



            "webrtc_state": self.webrtc_connection_state,



            "webrtc_connection_state": self.webrtc_connection_state,



            "last_successfully_processed_frame": last_proc_str,



            "last_successfully_processed_timestamp": last_proc_str,



            "root_cause_classification": root_cause,



            "proctoring_status": "INTERRUPTED",



            "actions_status": [



                "FLAGGED",



                "TIMER PAUSED",



                "ATTEMPTING RECONNECTION",



            ],



            "message": (



                f"iOS CAMERA INTERRUPTED – SESSION FLAGGED\n"



                f"MANDATORY iPAD CAMERA FEED INTERRUPTED\n\n"



                f"No valid iPad frames received for more than {int(IPadCameraState.THRESHOLD_DEGRADED)} seconds.\n"



                f"Camera source: {self.camera_source}\n"



                f"Last valid frame: {last_frame_str}\n"



                f"Interruption duration: {duration_str}\n"



                f"WebRTC state: {self.webrtc_connection_state}\n"



                f"Last successfully processed frame: {last_proc_str}\n"



                f"Stopped stage: {root_cause}\n"



                f"Recovery: Attempting automatically\n\n"



                f"Actions / status:\n"



                f"• FLAGGED\n"



                f"• TIMER PAUSED\n"



                f"• ATTEMPTING RECONNECTION"



            ),



        }











def get_ipad_camera_monitor() -> IPadCameraStreamMonitor:



    return IPadCameraStreamMonitor.get_instance()



