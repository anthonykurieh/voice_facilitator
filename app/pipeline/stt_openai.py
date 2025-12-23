import io
import queue
import sys
import threading
from dataclasses import dataclass
from typing import Optional, List

import numpy as np
import sounddevice as sd
import soundfile as sf

from app.pipeline.openai_client import client
from app.config import (
    SAMPLE_RATE,
    CHANNELS,
    RECORD_MAX_SECONDS,
    FRAME_DURATION_SEC,
    STT_MODEL,
)


@dataclass
class VadConfig:
    # How long to listen for the *start* of speech
    max_wait_seconds: float = 6.0

    # Trailing silence required to stop recording
    silence_seconds: float = 1.0

    # Pre-buffer so we don’t clip the first syllable
    prebuffer_seconds: float = 0.4

    # Dynamic threshold controls
    # start_thresh = max(min_start, noise_rms * start_mult)
    # stop_thresh  = max(min_stop,  noise_rms * stop_mult)
    start_mult: float = 3.0
    stop_mult: float = 2.0
    min_start: float = 0.010
    min_stop: float = 0.012


class OpenAISTT:
    def __init__(self, model: str = STT_MODEL, vad: Optional[VadConfig] = None):
        self.model = model
        self.vad = vad or VadConfig()

    def _audio_to_wav_bytes(self, audio: np.ndarray) -> bytes:
        buf = io.BytesIO()
        sf.write(buf, audio, SAMPLE_RATE, format="WAV", subtype="PCM_16")
        buf.seek(0)
        return buf.read()

    def _rms(self, frame: np.ndarray) -> float:
        return float(np.sqrt(np.mean(np.square(frame))))

    def _record_until_silence(self) -> np.ndarray:
        print("[STT] Listening... speak when ready.")

        frame_samples = int(SAMPLE_RATE * FRAME_DURATION_SEC)
        silence_frames_required = int(self.vad.silence_seconds / FRAME_DURATION_SEC)
        max_frames_total = int(RECORD_MAX_SECONDS / FRAME_DURATION_SEC)
        max_wait_frames = int(self.vad.max_wait_seconds / FRAME_DURATION_SEC)

        # prebuffer (ring buffer)
        prebuffer_frames = max(1, int(self.vad.prebuffer_seconds / FRAME_DURATION_SEC))
        prebuf: List[np.ndarray] = []

        audio_queue: "queue.Queue[np.ndarray]" = queue.Queue()
        stop_event = threading.Event()

        def callback(indata, frames, time_info, status):
            if status:
                print(f"[STT] Recording status: {status}", file=sys.stderr)
            if stop_event.is_set():
                return
            # mono float32
            data = indata.copy()
            if data.ndim > 1:
                data = data.mean(axis=1, keepdims=True)
            audio_queue.put(data.astype("float32"))

        # 1) Calibrate noise for ~0.5s
        noise_frames = []
        calibrate_frames = max(1, int(0.5 / FRAME_DURATION_SEC))

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            blocksize=frame_samples,
            callback=callback,
        ):
            for _ in range(calibrate_frames):
                try:
                    f = audio_queue.get(timeout=1.0)
                    noise_frames.append(self._rms(f))
                except queue.Empty:
                    pass

            noise_rms = float(np.median(noise_frames)) if noise_frames else 0.002

            start_thresh = max(self.vad.min_start, noise_rms * self.vad.start_mult)
            stop_thresh = max(self.vad.min_stop, noise_rms * self.vad.stop_mult)

            print(
                f"[STT] Calibrated noise_rms={noise_rms:.5f} "
                f"start_thresh={start_thresh:.5f} stop_thresh={stop_thresh:.5f} "
                f"(silence_frames_required={silence_frames_required})"
            )

            frames: List[np.ndarray] = []
            speaking = False
            silence_count = 0
            waited = 0

            while True:
                try:
                    frame = audio_queue.get(timeout=RECORD_MAX_SECONDS)
                except queue.Empty:
                    break

                r = self._rms(frame)

                # maintain prebuffer always
                prebuf.append(frame)
                if len(prebuf) > prebuffer_frames:
                    prebuf.pop(0)

                if not speaking:
                    waited += 1
                    if r >= start_thresh:
                        speaking = True
                        frames.extend(prebuf)  # include the lead-in
                        prebuf = []
                        silence_count = 0
                        print("[STT] Speech detected, recording started.")
                    elif waited >= max_wait_frames:
                        print("[STT] No speech detected (timeout).")
                        stop_event.set()
                        break
                    continue

                # speaking
                frames.append(frame)

                if r < stop_thresh:
                    silence_count += 1
                else:
                    silence_count = 0

                if silence_count >= silence_frames_required:
                    print("[STT] Detected pause in speech, stopping recording.")
                    stop_event.set()
                    break

                if len(frames) >= max_frames_total:
                    print("[STT] Reached maximum recording duration, stopping.")
                    stop_event.set()
                    break

        if not frames:
            return np.zeros(0, dtype="float32")

        audio = np.concatenate(frames, axis=0)
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
        text = transcription.text.strip()
        print(f"[STT] Transcription: {text}")
        return text