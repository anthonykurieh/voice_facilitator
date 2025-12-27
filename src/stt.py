"""Speech-to-Text module for real-time voice transcription."""
import sounddevice as sd
import numpy as np
import os
from openai import OpenAI
from typing import Optional, Callable
import threading
import queue
import time
from src.config import (
    SAMPLE_RATE, CHANNELS, RECORD_MAX_SECONDS, FRAME_DURATION_SEC,
    SILENCE_DURATION_SEC, ENERGY_FLOOR, ENERGY_CEIL, STOP_THRESHOLD_RATIO,
    STT_MODEL
)


class SpeechToText:
    """Real-time microphone listener with OpenAI Whisper transcription."""
    
    def __init__(self, api_key: str):
        """Initialize STT.
        
        Args:
            api_key: OpenAI API key
        """
        self.client = OpenAI(api_key=api_key)
        self.model = STT_MODEL
        
        # Audio settings from config
        self.sample_rate = SAMPLE_RATE
        self.channels = CHANNELS
        self.frame_duration_sec = FRAME_DURATION_SEC
        self.chunk_size = int(self.sample_rate * self.frame_duration_sec)
        self.record_max_seconds = RECORD_MAX_SECONDS
        self.silence_duration_sec = SILENCE_DURATION_SEC
        
        # Energy thresholds
        self.energy_floor = ENERGY_FLOOR
        self.energy_ceil = ENERGY_CEIL
        self.stop_threshold_ratio = STOP_THRESHOLD_RATIO
        
        # Calibrated thresholds (set during calibration)
        self.start_threshold = None
        self.stop_threshold = None
        
        self.is_listening = False
        self.audio_queue = queue.Queue()
        self.calibrated = False
        self.recording = None  # Will hold the recording array
    
    def _calculate_energy(self, audio_data: np.ndarray) -> float:
        """Calculate energy level of audio frame."""
        # sounddevice returns float32 in range [-1.0, 1.0]
        return np.abs(audio_data).mean()
    
    def calibrate(self, duration: float = 2.0):
        """Calibrate noise level from ambient sound.
        
        Args:
            duration: Seconds to sample for calibration
        """
        print("Calibrating noise level... (please be quiet)")
        
        # Record ambient sound
        recording = sd.rec(
            int(duration * self.sample_rate),
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype='float32'
        )
        sd.wait()  # Wait until recording is finished
        
        # Calculate energy for each frame
        frame_size = self.chunk_size
        energies = []
        for i in range(0, len(recording), frame_size):
            chunk = recording[i:i+frame_size]
            if len(chunk) > 0:
                energy = self._calculate_energy(chunk.flatten())
                energies.append(energy)
        
        # Calculate ambient noise level
        ambient_energy = np.mean(energies) if energies else self.energy_floor
        
        # Set start threshold: clamp between floor and ceil, or use ambient * 2
        start_thresh = max(ambient_energy * 2, self.energy_floor)
        start_thresh = min(start_thresh, self.energy_ceil)
        
        self.start_threshold = start_thresh
        self.stop_threshold = start_thresh * self.stop_threshold_ratio
        
        self.calibrated = True
        print(f"Calibration complete. Start threshold: {self.start_threshold:.4f}, Stop threshold: {self.stop_threshold:.4f}")
    
    def listen(self, on_transcription: Optional[Callable[[str], None]] = None) -> str:
        """Listen to microphone and transcribe speech.
        
        Args:
            on_transcription: Optional callback when transcription is ready
            
        Returns:
            Transcribed text
        """
        if not self.calibrated:
            self.calibrate()
        
        print("Listening... (speak now)")
        self.is_listening = True
        
        # Record audio with streaming callback
        recording_frames = []
        callback_state = {
            'silence_start': None,
            'speech_detected': False,
            'recording_start': time.time(),
            'should_stop': False
        }
        
        def audio_callback(indata, frames, time_info, status):
            """Callback function for audio stream."""
            if status:
                print(f"Audio status: {status}")
            
            if not self.is_listening or callback_state['should_stop']:
                raise sd.CallbackStop
            
            # Calculate energy
            energy = self._calculate_energy(indata.flatten())
            recording_frames.append(indata.copy())
            
            # Detect speech using start threshold
            if energy > self.start_threshold:
                callback_state['speech_detected'] = True
                callback_state['silence_start'] = None
            elif callback_state['speech_detected']:
                # Once speech detected, use stop threshold
                if energy <= self.stop_threshold:
                    if callback_state['silence_start'] is None:
                        callback_state['silence_start'] = time.time()
                    
                    # Stop if silence duration exceeded
                    if time.time() - callback_state['silence_start'] >= self.silence_duration_sec:
                        callback_state['should_stop'] = True
                        raise sd.CallbackStop
                else:
                    # Still above stop threshold, reset silence timer
                    callback_state['silence_start'] = None
            
            # Check max recording time
            if time.time() - callback_state['recording_start'] >= self.record_max_seconds:
                if len(recording_frames) > 0:
                    callback_state['should_stop'] = True
                    raise sd.CallbackStop
        
        try:
            # Start streaming
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype='float32',
                blocksize=self.chunk_size,
                callback=audio_callback
            ):
                # Wait for callback to stop or timeout
                while self.is_listening and not callback_state['should_stop']:
                    time.sleep(0.1)
                    if time.time() - callback_state['recording_start'] >= self.record_max_seconds + 1:
                        break
        
        except KeyboardInterrupt:
            pass
        except sd.CallbackStop:
            pass
        
        self.is_listening = False
        print("Processing speech...")
        
        if not recording_frames:
            return ""
        
        # Concatenate all frames
        recording = np.concatenate(recording_frames, axis=0)
        
        # Transcribe using OpenAI Whisper
        try:
            # Save to temporary file for API
            import tempfile
            import wave
            import struct
            
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp_file:
                # Convert float32 to int16 for WAV file
                audio_int16 = (recording * 32767).astype(np.int16)
                
                # Write as WAV file
                with wave.open(tmp_file.name, 'wb') as wf:
                    wf.setnchannels(self.channels)
                    wf.setsampwidth(2)  # 16-bit
                    wf.setframerate(self.sample_rate)
                    wf.writeframes(audio_int16.tobytes())
                
                # Transcribe
                with open(tmp_file.name, 'rb') as audio_file:
                    transcript = self.client.audio.transcriptions.create(
                        model=self.model,
                        file=audio_file,
                        language="en"
                    )
                
                text = transcript.text.strip()
                os.unlink(tmp_file.name)
                
                if on_transcription:
                    on_transcription(text)
                
                return text
        
        except Exception as e:
            print(f"Transcription error: {e}")
            return ""
    
    def stop(self):
        """Stop listening."""
        self.is_listening = False
    
    def cleanup(self):
        """Clean up resources."""
        self.stop()

