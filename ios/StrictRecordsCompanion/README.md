# StrictRecords – Native iPad Screen Sharing Reference Implementation

This directory provides native iOS/iPadOS companion reference implementations for full-device screen capture and WebRTC media streaming to Windows Strict Session proctoring.

## Architecture

1. **iPadOS 16+ (ScreenCaptureKit & SCContentSharingPicker)**:
   - File: `ScreenCaptureKitEngine.swift`
   - Uses Apple's modern ScreenCaptureKit framework with `SCContentSharingPicker`.
   - Allows user to authorize whole-display capture or specific app window scopes.
   - Enforces 15 FPS frame rate and zero audio capture (`capturesAudio = false`).

2. **Older iPadOS Versions & Universal Whole-Device Capture (ReplayKit Broadcast Extension)**:
   - File: `BroadcastUploadExtension/SampleHandler.swift`
   - Target: Broadcast Upload Extension (`com.strictrecords.ios.companion.broadcast`)
   - Uses `RPBroadcastSampleHandler` with App Group `group.com.strictrecords.ios.companion`.
   - Captures `CMSampleBuffer` frames across all apps, even when the user switches to other study apps (e.g. books, calculators, notes).
   - Rate-limited to 12-15 FPS to conserve battery and bandwidth.
   - Strictly ignores `.audioApp` and `.audioMic` to comply with the zero-audio privacy policy.

## WebRTC Signaling & Windows Host Bridge

- Screen share video frames are transmitted as a separate video track labeled `IOS_SCREEN_SHARE` on the authenticated WebRTC connection.
- Windows host validates multi-track video SDP offers (`1 <= len(media) <= 2`) and routes the screen track to `IPadScreenShareMonitor`.
- Windows proctoring continuously calculates Mean Absolute Difference (MAD) to detect activity (`STATIC`, `ACTIVE`, `RAPID_TRANSITION`) and runs an independent 10-second interruption watchdog.
