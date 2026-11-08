# Wake-up camera validation and recovery

## Reproduction and confirmed defects

The 2026-10-12 recording shows a visible face and good lighting while the legacy `android.media.FaceDetector` reports no face for about eight seconds. The old production path resized a preview to 240×320, then reduced it to 160 pixels wide and ran the detector twice. It timed samples using the scan timer rather than camera frame timestamps. A 3.5-second absence forced a complete restart. Camera disconnection released resources without restoring the retry button. The live scan used 65% while saved proof and the server require 70%.

Replay on Samsung SM-A556E using 32 cropped preview frames:

| Legacy width | Detected | Quality valid | Average detection time |
|---|---:|---:|---:|
| 160 | 2/32 | 2/32 | 4.0 ms |
| 320 | 11/32 | 1/32 | 15.8 ms |
| 480 | 8/32 | 1/32 | 37.3 ms |
| 640 | 8/32 | 2/32 | 68.3 ms |

These measurements establish that increasing resolution alone does not solve the legacy detector's misses and inaccurate landmarks. Frame replay is a reproduction test, not a live proof.

The bundled offline ML Kit detector was also measured on the same device and frames:

| Input width | Detected | Quality valid | Average detection time |
|---|---:|---:|---:|
| 320 | 28/32 | 20/32 | 45.2 ms |
| 480 | 32/32 | 20/32 | 35.3 ms |
| 640 | 32/32 | 23/32 | 44.4 ms |

The production input is 480 pixels wide while preserving source aspect ratio. Quality checks remain mandatory.

## October 13 rejection

The phone retained a camera image and policy v2 measurements from the actual 09:23 attempt: 20,191 ms of valid observation, 96/100 valid samples, and a fresh final frame. `completeCameraStep()` stored `wakeup_camera_path`, but `getDayState()` omitted that column. The UI's read-back validator consequently looked at an empty path and rejected the valid saved proof. The path is now exported and specific metadata/file/hash failures are surfaced. An isolated save/reload instrumentation regression is built; execution on the updated phone remains NOT VERIFIED.

Camera completion is separate from final wake-up acceptance. Selected-earbud routing is measured from the actual MediaPlayer route, then the full configured asset (currently eight seconds) must be measured without seeking. All results bind to one attempt. Final attendance and configured proof points wait for authoritative approval; repeated submission/approval is idempotent. Demo results cannot enter the production submission path.

## Authoritative policy

`WakeUpCameraPolicy` is used by production, the isolated demo and persisted proof validation:

- At least 20,000 ms of fresh **valid observation**, excluding paused intervals.
- At least 40 valid, unique samples; valid/eligible ratio at least 0.70, calculated from exact counts. Zero samples never pass.
- One suitably positioned face, suitable lighting, visible eye landmarks and suitable head pose.
- Three consecutive recent suitable observations before counting resumes. No old successful frame substitutes for a missing face.
- Final evidence at most 750 ms old; ordered observation timestamps and consistent metadata.
- A maximum session interval of 180 seconds.
- Production revalidates the same metadata and retained image hash before completing the camera step and before completing the wake-up flow. Points still require the existing proof approval process.

The 70% requirement preserves the existing production database and server policy. It is not reduced to make the preview pass. This is 2D camera presence verification, not identity recognition or spoof prevention.

## Recovery

The camera owns one capture session and one bounded analysis worker. ImageReader timestamps identify actual new frames. Capture and detector processing run separately. Generation checks discard callbacks from closed sessions.

Face or lighting loss pauses counting. Recovery requires fresh stable frames and does not count any time spent without valid evidence. Progress can be retained within a configurable 30-second grace interval; longer interruption starts a new observation interval. The grace is an explicit safety bound, not a claim of a universally optimal value. Its suitability must be judged with the real-device test results.

Camera errors or stalled frames close the old resources and attempt up to three reopens, spaced 0.5/1/1.5 seconds apart. Exhausted recovery gives a Retry action. Backgrounding or locking releases the camera and clears readiness. Resume reopens it. Denied permission never supplies valid samples.

## Diagnostics and test separation

`StrictWakeCamera` logs frame dimensions, unique frame timestamps, processing time, age, face count, lighting, stable observations, camera errors and recovery attempts. It does not log or retain routine camera images. Only a successful production camera step retains its required proof image.

Settings → Wake-Up Demo Check runs the same real frame source and a 20-second observation policy without writing attendance, score, proof review or restriction state. The separate 10-second camera check is a readiness diagnostic, not a production proof. Hardware availability is not reported as successful face detection.

Sources: [Android FaceDetector format requirements](https://developer.android.com/reference/android/media/FaceDetector), [Camera preview orientation](https://developer.android.com/media/camera/camera2/camera-preview), [ML Kit bundled face detector and camera image rotation](https://developers.google.com/ml-kit/vision/face-detection/android).

## Verification status

See `scratch/face-video-review/` for actual build logs and recorded-frame benchmark output. Live-device results are recorded separately; a successful build or frame replay does not establish a successful live interruption test.

The delivered APK is v1.15.13 (41), with 71 Android unit tests passing. The phone disconnected before new camera, interruption, route, playback, and save/reload instrumentation runs. These checks, installation, and a fresh installed-app screenshot are NOT VERIFIED. The user chose to check the desktop APK later. See `reports/2026-10-13-wake-up-hourly-fixes.md` for the full delivery record.
