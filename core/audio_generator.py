"""
Generates a real playable PCM WAV audio file for the Wake-Up Motivation Track
so both Android (MediaPlayer) and Windows/Web audio players play genuine audio
with accurate duration tracking.
"""

import math
import os
import struct
import wave


def ensure_motivation_wav(target_path: str, duration_sec: int = 8, sample_rate: int = 22050) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
    if os.path.exists(target_path) and os.path.getsize(target_path) > 1000:
        return target_path

    num_samples = duration_sec * sample_rate
    # Rising disciplined chord progression (C4 -> E4 -> G4 -> C5)
    freqs = [261.63, 329.63, 392.00, 523.25]

    with wave.open(target_path, "w") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)  # 16-bit PCM
        wav_file.setframerate(sample_rate)

        frames = bytearray()
        for i in range(num_samples):
            t = i / sample_rate
            freq = freqs[int(t * 2) % len(freqs)]
            envelope = min(1.0, (i / 1000.0)) * (0.6 + 0.4 * math.sin(2 * math.pi * 2.0 * t))
            sample_val = int(12000 * envelope * math.sin(2 * math.pi * freq * t))
            frames.extend(struct.pack("<h", max(-32767, min(32767, sample_val))))

        wav_file.writeframes(bytes(frames))

    return target_path
