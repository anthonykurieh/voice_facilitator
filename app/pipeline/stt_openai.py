import io
import queue
import sys
import threading
from collections import deque
from typing import Optional, Deque, List, Tuple

import numpy as np
import sounddevice as sd
import soundfile as sf

from app.pipeline.openai_client import client
from app.config import (
    SAMPLE_RATE,
    CHANNELS,
    RECORD_MAX_SECONDS,
    SILENCE_THRESHOLD,       # kept for backward compatibility (used as a fallback)
    SILENCE_DURATION_SEC,
    FRAME_DURATION_SEC,
    STT_MODEL,
)


class OpenAISTT:
    """
    STT wrapper that:
    - Records from the default microphone
    - Uses an energy-based VAD with:
        * noise floor calibration
        * speech-start gating (prevents noise triggering recording)
        * hysteresis (stable start/stop)
        * pre-roll (avoid clipping first syllable)
    - Stops when the user is silent for a bit OR max duration is reached
    - Sends the audio to OpenAI STT and returns the transcript
    """

    def __init__(self, model: str = STT_MODEL):
        self.model = model

        # ---- Optional tunables (safe defaults) ----
        # If you later add these to app.config, we’ll use them automatically.
        self.calibration_sec = float(getattr(__import__("app.config", fromlist=["x"]), "VAD_CALIBRATION_SEC", 0.6))
        self.start_speech_frames_required = int(getattr(__import__("app.config", fromlist=["x"]), "VAD_START_FRAMES", 6))
        self.pre_roll_sec = float(getattr(__import__("app.config", fromlist=["x"]), "VAD_PRE_ROLL_SEC", 0.25))
        self.min_record_sec = float(getattr(__import__("app.config", fromlist=["x"]), "VAD_MIN_RECORD_SEC", 0.5))

        # Multipliers relative to measured noise floor
        self.start_multiplier = float(getattr(__import__("app.config", fromlist=["x"]), "VAD_START_MULTIPLIER", 3.0))
        self.stop_multiplier = float(getattr(__import__("app.config", fromlist=["x"]), "VAD_STOP_MULTIPLIER", 2.0))

        # Absolute floors so threshold never becomes too tiny
        self.abs_start_floor = float(getattr(__import__("app.config", fromlist=["x"]), "VAD_ABS_START_FLOOR", 0.012))
        self.abs_stop_floor = float(getattr(__import__("app.config", fromlist=["x"]), "VAD_ABS_STOP_FLOOR", 0.010))

        # Backward compatible: if calibration is disabled, use SILENCE_THRESHOLD as baseline
        self.fallback_threshold = float(SILENCE_THRESHOLD)

    def _rms(self, frame: np.ndarray) -> float:
        """
        frame: (samples, channels) float32
        returns scalar RMS
        """
        # Convert to mono for RMS
        if frame.ndim == 2 and frame.shape[1] > 1:
            x = frame.mean(axis=1)
        elif frame.ndim == 2:
            x = frame[:, 0]
        else:
            x = frame
        return float(np.sqrt(np.mean(np.square(x)) + 1e-12))

    def _audio_to_wav_bytes(self, audio: np.ndarray) -> bytes:
        buf = io.BytesIO()
        sf.write(buf, audio, SAMPLE_RATE, format="WAV", subtype="PCM_16")
        buf.seek(0)
        return buf.read()

    def _calibrate_noise_floor(
        self,
        audio_queue: "queue.Queue[np.ndarray]",
        calibration_frames: int,
        timeout_sec: float,
    ) -> float:
        """
        Reads a few frames to estimate ambient noise RMS.
        Uses a robust median rather than mean.
        """
        rms_values: List[float] = []
        for _ in range(calibration_frames):
            try:
                frame = audio_queue.get(timeout=timeout_sec)
            except queue.Empty:
                break
            rms_values.append(self._rms(frame))

        if not rms_values:
            return self.fallback_threshold

        noise_rms = float(np.median(np.array(rms_values, dtype=np.float32)))
        return noise_rms

    def _record_until_silence(self) -> np.ndarray:
        """
        Record audio until we detect enough continuous silence (after speech has started)
        or hit max time. Returns a 1D NumPy float32 array.
        """
        print("[STT] Listening... speak when ready.")

        audio_queue: "queue.Queue[np.ndarray]" = queue.Queue()
        stop_event = threading.Event()

        frame_samples = int(SAMPLE_RATE * FRAME_DURATION_SEC)
        max_frames = int(RECORD_MAX_SECONDS / FRAME_DURATION_SEC)
        silence_frames_required = int(SILENCE_DURATION_SEC / FRAME_DURATION_SEC)
        min_record_frames = max(1, int(self.min_record_sec / FRAME_DURATION_SEC))

        pre_roll_frames = max(1, int(self.pre_roll_sec / FRAME_DURATION_SEC))
        calibration_frames = max(1, int(self.calibration_sec / FRAME_DURATION_SEC))

        # State machine
        speech_started = False
        consecutive_speech = 0
        consecutive_silence = 0
        total_frames = 0

        # buffers
        pre_roll: Deque[np.ndarray] = deque(maxlen=pre_roll_frames)
        frames: List[np.ndarray] = []

        # thresholds (set after calibration)
        start_thresh = None
        stop_thresh = None

        def callback(indata, frames_count, time, status):
            if status:
                print(f"[STT] Recording status: {status}", file=sys.stderr)
            if stop_event.is_set():
                return
            audio_queue.put(indata.copy())

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            blocksize=frame_samples,
            callback=callback,
        ):
            # --- Calibration phase (ambient noise) ---
            noise_rms = self._calibrate_noise_floor(
                audio_queue=audio_queue,
                calibration_frames=calibration_frames,
                timeout_sec=max(0.2, self.calibration_sec),
            )

            # Dynamic thresholds with floors + multipliers
            start_thresh = max(self.abs_start_floor, noise_rms * self.start_multiplier)
            stop_thresh = max(self.abs_stop_floor, noise_rms * self.stop_multiplier)

            # If calibration ended up weirdly low/high, keep some sanity with fallback
            # (SILENCE_THRESHOLD becomes "stop-ish" fallback)
            stop_thresh = max(stop_thresh, self.fallback_threshold)

            print(
                f"[STT] Calibrated noise_rms={noise_rms:.5f} "
                f"start_thresh={start_thresh:.5f} stop_thresh={stop_thresh:.5f} "
                f"(start_frames={self.start_speech_frames_required}, "
                f"silence_frames_required={silence_frames_required})"
            )

            # --- Recording loop ---
            while True:
                try:
                    frame = audio_queue.get(timeout=RECORD_MAX_SECONDS)
                except queue.Empty:
                    print("[STT] No audio received, stopping.")
                    break

                total_frames += 1
                pre_roll.append(frame)

                rms = self._rms(frame)

                if not speech_started:
                    # Gate speech start: need N consecutive frames above start threshold
                    if rms >= start_thresh:
                        consecutive_speech += 1
                    else:
                        consecutive_speech = 0

                    if consecutive_speech >= self.start_speech_frames_required:
                        speech_started = True
                        # Add pre-roll so we don't clip the beginning
                        frames.extend(list(pre_roll))
                        consecutive_silence = 0
                        # Reset counters for stop logic
                        # (We’ve already recorded some frames now)
                        print("[STT] Speech detected, recording started.")
                    else:
                        # Keep waiting for real speech
                        if total_frames >= max_frames:
                            print("[STT] Reached maximum wait/record duration, stopping.")
                            break
                        continue

                else:
                    # Actively recording
                    frames.append(frame)

                    # Silence detection uses stop threshold (hysteresis)
                    if rms < stop_thresh:
                        consecutive_silence += 1
                    else:
                        consecutive_silence = 0

                    # Stop only if:
                    # - recorded at least min_record_frames AND
                    # - trailing silence long enough
                    if len(frames) >= min_record_frames and consecutive_silence >= silence_frames_required:
                        print("[STT] Detected pause in speech, stopping recording.")
                        break

                    if total_frames >= max_frames:
                        print("[STT] Reached maximum recording duration, stopping.")
                        break

            stop_event.set()

        if not frames:
            return np.zeros(0, dtype="float32")

        audio = np.concatenate(frames, axis=0)

        # Flatten to mono
        if audio.ndim > 1:
            audio = audio.mean(axis=1)

        return audio.astype("float32")

    def record_and_transcribe(self) -> str:
        audio = self._record_until_silence()

        if audio.size == 0:
            print("[STT] No audio captured.")
            return ""

        wav_bytes = self._audio_to_wav_bytes(audio)
        print("[STT] Sending audio to OpenAI STT...")

        buf = io.BytesIO(wav_bytes)
        buf.name = "input.wav"

        transcription = client.audio.transcriptions.create(
            model=self.model,
            file=buf,
        )

        text = (transcription.text or "").strip()
        print(f"[STT] Transcription: {text}")
        return text