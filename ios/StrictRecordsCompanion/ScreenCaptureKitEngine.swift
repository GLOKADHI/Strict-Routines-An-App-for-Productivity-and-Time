//
//  ScreenCaptureKitEngine.swift
//  StrictRecordsCompanion
//
//  Whole-device and selected-scope screen capture on iPadOS 16+ using ScreenCaptureKit
//  and SCContentSharingPicker. Video-only encrypted media streaming to Windows EXE.
//

import Foundation
import CoreMedia
import ScreenCaptureKit
import WebRTC

@available(iOS 16.0, *)
public final class ScreenCaptureKitEngine: NSObject, SCStreamOutput, SCStreamDelegate {
    public static let shared = ScreenCaptureKitEngine()
    
    private var stream: SCStream?
    private var filter: SCContentFilter?
    private var configuration: SCStreamConfiguration?
    private let picker = SCContentSharingPicker.shared
    
    private var isCapturing = false
    public var onVideoFrameCaptured: ((CMSampleBuffer) -> Void)?
    
    private override init() {
        super.init()
    }
    
    public func setupPicker(delegate: SCContentSharingPickerObserver) {
        picker.add(delegate)
        picker.isActive = true
    }
    
    public func startCapture(contentFilter: SCContentFilter) async throws {
        guard !isCapturing else { return }
        self.filter = contentFilter
        
        let config = SCStreamConfiguration()
        config.width = 1280
        config.height = 720
        config.minimumFrameInterval = CMTime(value: 1, timescale: 15) // Max 15 FPS
        config.pixelFormat = kCVPixelFormatType_32BGRA
        config.showsCursor = true
        config.capturesAudio = false // Strictly zero microphone or system audio
        self.configuration = config
        
        let newStream = SCStream(filter: contentFilter, configuration: config, delegate: self)
        try newStream.addStreamOutput(self, type: .screen, sampleHandlerQueue: DispatchQueue(label: "com.strictrecords.screencap.queue"))
        try await newStream.startCapture()
        
        self.stream = newStream
        self.isCapturing = true
    }
    
    public func stopCapture() async throws {
        guard isCapturing, let stream = stream else { return }
        try await stream.stopCapture()
        self.stream = nil
        self.isCapturing = false
    }
    
    // MARK: - SCStreamOutput
    public func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .screen else { return } // Reject any audio buffers
        guard sampleBuffer.isValid else { return }
        onVideoFrameCaptured?(sampleBuffer)
    }
    
    // MARK: - SCStreamDelegate
    public func stream(_ stream: SCStream, didStopWithError error: Error) {
        isCapturing = false
        self.stream = nil
    }
}
