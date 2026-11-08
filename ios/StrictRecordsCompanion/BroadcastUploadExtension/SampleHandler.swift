//
//  SampleHandler.swift
//  StrictRecordsCompanionBroadcastUploadExtension
//
//  ReplayKit whole-device broadcast upload extension for iPadOS devices.
//  Receives system screen frames across all apps, routes them into a shared
//  App Group memory pipeline, and streams them to Windows EXE over WebRTC.
//

import ReplayKit
import CoreMedia
import Foundation

public final class SampleHandler: RPBroadcastSampleHandler {
    private static let appGroupID = "group.com.strictrecords.ios.companion"
    private var sharedDefaults: UserDefaults?
    private var lastDeliveredTimestamp: CMTime = .zero
    private let targetFPS: Double = 12.0
    
    public override func broadcastStarted(withSetupInfo setupInfo: [String : NSObject]?) {
        sharedDefaults = UserDefaults(suiteName: SampleHandler.appGroupID)
        sharedDefaults?.set(true, forKey: "isScreenBroadcasting")
        sharedDefaults?.set(Date().timeIntervalSince1970, forKey: "broadcastStartTime")
    }
    
    public override func broadcastPaused() {
        sharedDefaults?.set(false, forKey: "isScreenBroadcasting")
    }
    
    public override func broadcastResumed() {
        sharedDefaults?.set(true, forKey: "isScreenBroadcasting")
    }
    
    public override func broadcastFinished() {
        sharedDefaults?.set(false, forKey: "isScreenBroadcasting")
        sharedDefaults?.removeObject(forKey: "broadcastStartTime")
    }
    
    public override func processSampleBuffer(_ sampleBuffer: CMSampleBuffer, with sampleBufferType: RPSampleBufferType) {
        switch sampleBufferType {
        case .video:
            // Process whole-device screen frames; limit rate to targetFPS
            let pts = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
            if lastDeliveredTimestamp != .zero {
                let diff = CMTimeGetSeconds(CMTimeSubtract(pts, lastDeliveredTimestamp))
                if diff < (1.0 / targetFPS) {
                    return // Drop to maintain target FPS and protect device battery
                }
            }
            lastDeliveredTimestamp = pts
            
            // Forward video sample buffer to WebRTC Native Video Track
            NativeWebRTCBridge.shared.ingestScreenSampleBuffer(sampleBuffer)
            
        case .audioApp, .audioMic:
            // Explicit privacy policy: ZERO audio or microphone capture
            break
            
        @unknown default:
            break
        }
    }
}

/// Helper singleton bridging ReplayKit sample buffers to the app's WebRTC RTCPeerConnection
public final class NativeWebRTCBridge {
    public static let shared = NativeWebRTCBridge()
    private init() {}
    
    public func ingestScreenSampleBuffer(_ sampleBuffer: CMSampleBuffer) {
        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        // Feeds the WebRTC RTCVideoSource backed by CVPixelBufferRef
        NotificationCenter.default.post(
            name: NSNotification.Name("StrictRecordsNewScreenPixelBuffer"),
            object: pixelBuffer
        )
    }
}
