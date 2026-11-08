path = 'core/server.py'
with open(path, 'r', encoding='utf-8') as f:
    text = f.read()

target = """        if path == "/api/youtube/educational/request":
            url = body.get("url") or ""
            vid = body.get("video_id") or extract_canonical_video_id(url) or ""
            clean_vid = extract_canonical_video_id(vid) or vid.strip()
            if not clean_vid:
                self._send_json({"success": False, "error": "Valid YouTube URL or video ID is required."}, status=400)
                return

            req_id = body.get("request_id") or f"req_{uuid.uuid4().hex[:12]}"
            val_sess_id = body.get("validation_session_id") or f"val_{uuid.uuid4().hex[:12]}"
            user_id = body.get("user_id") or body.get("account_id") or "user_lok"
            dev_id = body.get("device_id") or "BROWSER_EXT"
            v_title = body.get("title") or "YouTube Video"
            v_channel = body.get("channel") or ""

            existing = engine.get_educational_approval(clean_vid, account_id=user_id)
            if existing and existing.get("decision") in ("EDUCATIONAL", "NOT_EDUCATIONAL"):
                if existing.get("decision") == "EDUCATIONAL":
                    engine.start_or_resume_playback_session(clean_vid, account_id=user_id, device_id=dev_id)
                    if self.db:
                        exp_ms = int(now_ist().timestamp() * 1000) + (existing.get("duration_seconds", 1205) + 300) * 1000
                        self.db.set_config_value("educational_unlock_active", "1")
                        self.db.set_config_value("educational_active_video_id", clean_vid)
                        self.db.set_config_value("educational_unlock_reason", f"Educational video approved: {v_title}")
                        self.db.set_config_value("educational_unlock_expires_at", str(exp_ms))
                    if self.restriction_mgr:
                        self.restriction_mgr.clear_caches()
                self._send_json({"success": True, "cached": True, **existing})
                return

            # Auto-enrich metadata if description, title, or duration are missing/generic
            if (not body.get("description") and not body.get("captions_text")) or not body.get("duration_seconds") or not body.get("title") or body.get("title") == "YouTube Video":
                try:
                    from windows.strictrecords_native_host import extract_metadata
                    meta_res = extract_metadata(url or f"https://www.youtube.com/watch?v={clean_vid}", clean_vid)
                    if meta_res.get("success"):
                        if not body.get("title") or body.get("title") == "YouTube Video":
                            v_title = meta_res.get("title") or v_title
                        if not body.get("channel"):
                            v_channel = meta_res.get("channel") or v_channel
                        if not body.get("duration_seconds"):
                            body["duration_seconds"] = meta_res.get("duration_seconds")
                        if not body.get("description"):
                            body["description"] = meta_res.get("description")
                        if not body.get("chapters"):
                            body["chapters"] = meta_res.get("chapters")
                except Exception as meta_err:
                    logger.warning("Could not auto-fetch video metadata via native host: %s", meta_err)

            eval_result = evaluator.evaluate_video(
                url=url,
                video_id=clean_vid,
                title=v_title,
                channel=v_channel,
                duration_seconds=body.get("duration_seconds"),
                captions_text=body.get("captions_text"),
                description=body.get("description"),
                chapters=body.get("chapters"),
                language=body.get("language"),
                user_id=user_id,
                device_id=dev_id,
                request_id=req_id,
                validation_session_id=val_sess_id,
                timeout=30.0,
            )

            canonical_url = eval_result.get("canonical_url") or f"https://www.youtube.com/watch?v={clean_vid}"
            decision = eval_result.get("decision", "PROCESSING_ERROR")
            dur_sec = eval_result.get("duration_seconds", 0)
            dur_fmt = eval_result.get("duration_formatted", "Unknown")

            saved = engine.save_educational_approval(
                video_id=clean_vid,
                canonical_url=canonical_url,
                decision=decision,
                duration_seconds=dur_sec,
                duration_formatted=dur_fmt,
                reason=eval_result.get("reason"),
                confidence=eval_result.get("confidence"),
                timeline=eval_result.get("timeline"),
                title=v_title,
                channel=v_channel,
                account_id=user_id,
                review_status=eval_result.get("review_status", "COMPLETED"),
            )

            if self.db:
                hist_id = f"hist_{clean_vid}_{uuid.uuid4().hex[:8]}"
                self.db.save_educational_validation_history(
                    history_id=hist_id,
                    request_id=req_id,
                    validation_session_id=val_sess_id,
                    video_id=clean_vid,
                    canonical_url=canonical_url,
                    decision=decision,
                    duration_seconds=dur_sec,
                    duration_formatted=dur_fmt,
                    title=v_title,
                    channel=v_channel,
                    reason=eval_result.get("reason"),
                    confidence=eval_result.get("confidence"),
                    review_status=eval_result.get("review_status", "COMPLETED"),
                    playback_completion_state="NOT_STARTED",
                    account_id=user_id,
                    device_id=dev_id,
                )

            if decision == "EDUCATIONAL":
                engine.start_or_resume_playback_session(clean_vid, account_id=user_id, device_id=dev_id)
                if self.db:
                    exp_ms = int(now_ist().timestamp() * 1000) + (dur_sec + 300) * 1000
                    self.db.set_config_value("educational_unlock_active", "1")
                    self.db.set_config_value("educational_active_video_id", clean_vid)
                    self.db.set_config_value("educational_unlock_reason", f"Educational video approved: {v_title}")
                    self.db.set_config_value("educational_unlock_expires_at", str(exp_ms))
                if self.restriction_mgr:
                    self.restriction_mgr.clear_caches()

            self._send_json({"success": True, **saved, "taufa_result": eval_result})
            return"""

replacement = """        if path == "/api/youtube/educational/request":
            try:
                url = body.get("url") or ""
                vid = body.get("video_id") or extract_canonical_video_id(url) or ""
                clean_vid = extract_canonical_video_id(vid) or vid.strip()
                if not clean_vid:
                    self._send_json({"success": False, "error": "Valid YouTube URL or video ID is required."}, status=400)
                    return

                req_id = body.get("request_id") or f"req_{uuid.uuid4().hex[:12]}"
                val_sess_id = body.get("validation_session_id") or f"val_{uuid.uuid4().hex[:12]}"
                user_id = body.get("user_id") or body.get("account_id") or "user_lok"
                dev_id = body.get("device_id") or "BROWSER_EXT"
                v_title = body.get("title") or "YouTube Video"
                v_channel = body.get("channel") or ""

                existing = engine.get_educational_approval(clean_vid, account_id=user_id)
                if existing and existing.get("decision") in ("EDUCATIONAL", "NOT_EDUCATIONAL"):
                    if existing.get("decision") == "EDUCATIONAL":
                        engine.start_or_resume_playback_session(clean_vid, account_id=user_id, device_id=dev_id)
                        if self.db:
                            exp_ms = int(now_ist().timestamp() * 1000) + (existing.get("duration_seconds", 1205) + 300) * 1000
                            self.db.set_config_value("educational_unlock_active", "1")
                            self.db.set_config_value("educational_active_video_id", clean_vid)
                            self.db.set_config_value("educational_unlock_reason", f"Educational video approved: {v_title}")
                            self.db.set_config_value("educational_unlock_expires_at", str(exp_ms))
                        if self.restriction_mgr:
                            self.restriction_mgr.clear_caches()
                    self._send_json({"success": True, "cached": True, **existing})
                    return

                # Auto-enrich metadata if description, title, or duration are missing/generic
                if (not body.get("description") and not body.get("captions_text")) or not body.get("duration_seconds") or not body.get("title") or body.get("title") == "YouTube Video":
                    try:
                        from windows.strictrecords_native_host import extract_metadata
                        meta_res = extract_metadata(url or f"https://www.youtube.com/watch?v={clean_vid}", clean_vid)
                        if meta_res.get("success"):
                            if not body.get("title") or body.get("title") == "YouTube Video":
                                v_title = meta_res.get("title") or v_title
                            if not body.get("channel"):
                                v_channel = meta_res.get("channel") or v_channel
                            if not body.get("duration_seconds"):
                                body["duration_seconds"] = meta_res.get("duration_seconds")
                            if not body.get("description"):
                                body["description"] = meta_res.get("description")
                            if not body.get("chapters"):
                                body["chapters"] = meta_res.get("chapters")
                    except Exception as meta_err:
                        logger.warning("Could not auto-fetch video metadata via native host: %s", meta_err)

                eval_result = evaluator.evaluate_video(
                    url=url,
                    video_id=clean_vid,
                    title=v_title,
                    channel=v_channel,
                    duration_seconds=body.get("duration_seconds"),
                    captions_text=body.get("captions_text"),
                    description=body.get("description"),
                    chapters=body.get("chapters"),
                    language=body.get("language"),
                    user_id=user_id,
                    device_id=dev_id,
                    request_id=req_id,
                    validation_session_id=val_sess_id,
                    timeout=30.0,
                )

                canonical_url = eval_result.get("canonical_url") or f"https://www.youtube.com/watch?v={clean_vid}"
                decision = eval_result.get("decision", "PROCESSING_ERROR")
                dur_sec = eval_result.get("duration_seconds", 0)
                dur_fmt = eval_result.get("duration_formatted", "Unknown")

                saved = engine.save_educational_approval(
                    video_id=clean_vid,
                    canonical_url=canonical_url,
                    decision=decision,
                    duration_seconds=dur_sec,
                    duration_formatted=dur_fmt,
                    reason=eval_result.get("reason"),
                    confidence=eval_result.get("confidence"),
                    timeline=eval_result.get("timeline"),
                    title=v_title,
                    channel=v_channel,
                    account_id=user_id,
                    review_status=eval_result.get("review_status", "COMPLETED"),
                )

                if self.db:
                    hist_id = f"hist_{clean_vid}_{uuid.uuid4().hex[:8]}"
                    self.db.save_educational_validation_history(
                        history_id=hist_id,
                        request_id=req_id,
                        validation_session_id=val_sess_id,
                        video_id=clean_vid,
                        canonical_url=canonical_url,
                        decision=decision,
                        duration_seconds=dur_sec,
                        duration_formatted=dur_fmt,
                        title=v_title,
                        channel=v_channel,
                        reason=eval_result.get("reason"),
                        confidence=eval_result.get("confidence"),
                        review_status=eval_result.get("review_status", "COMPLETED"),
                        playback_completion_state="NOT_STARTED",
                        account_id=user_id,
                        device_id=dev_id,
                    )

                if decision == "EDUCATIONAL":
                    engine.start_or_resume_playback_session(clean_vid, account_id=user_id, device_id=dev_id)
                    if self.db:
                        exp_ms = int(now_ist().timestamp() * 1000) + (dur_sec + 300) * 1000
                        self.db.set_config_value("educational_unlock_active", "1")
                        self.db.set_config_value("educational_active_video_id", clean_vid)
                        self.db.set_config_value("educational_unlock_reason", f"Educational video approved: {v_title}")
                        self.db.set_config_value("educational_unlock_expires_at", str(exp_ms))
                    if self.restriction_mgr:
                        self.restriction_mgr.clear_caches()

                self._send_json({"success": True, **saved, "taufa_result": eval_result})
                return
            except Exception as req_ex:
                logger.error("Error handling educational request: %s", req_ex, exc_info=True)
                self._send_json({
                    "success": False,
                    "decision": "PROCESSING_ERROR",
                    "reason": f"Evaluation error: {str(req_ex)[:160]}",
                    "error": str(req_ex),
                    "review_status": "MANUAL_REVIEW"
                })
                return"""

norm_t = text.replace('\r\n', '\n')
norm_tgt = target.replace('\r\n', '\n')
norm_rep = replacement.replace('\r\n', '\n')

if norm_tgt in norm_t:
    new_t = norm_t.replace(norm_tgt, norm_rep, 1)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(new_t)
    print("Successfully patched core/server.py")
else:
    print("Could not find target in core/server.py")
