import io
import queue
import sys
import threading
from collections import deque
from typing import Deque, List

import numpy as np
import sounddevice as sd
import soundfile as sf

from app.pipeline.openai_client import client
from app.config import (
    SAMPLE_RATE,
    CHANNELS,
    INPUT_DEVICE,
    RECORD_MAX_SECONDS,
    STT_MODEL,
    WAIT_FOR_SPEECH_SECONDS,
    FRAME_DURATION_SEC,
    CALIBRATION_SEC,
    PRE_ROLL_SEC,
    MIN_RECORD_SECONDS,
    SILENCE_DURATION_SEC,
    MIN_START_THRESH,
    MAX_START_THRESH,
    START_MULTIPLIER,
    STOP_MULTIPLIER,
    START_FRAMES_REQUIRED,
)


class OpenAISTT:
    def __init__(self, model: str = STT_MODEL):
        self.model = model

    @staticmethod
    def _rms(frame: np.ndarray) -> float:
        return float(np.sqrt(np.mean(np.square(frame))))

    @staticmethod
    def _is_dead(frame: np.ndarray) -> bool:
        # if mic is muted / permissions / wrong device, frames can be near-zero
        return float(np.max(np.abs(frame))) < 1e-4

    def _audio_to_wav_bytes(self, audio: np.ndarray) -> bytes:
        buf = io.BytesIO()
        sf.write(buf, audio, SAMPLE_RATE, format="WAV", subtype="PCM_16")
        buf.seek(0)
        return buf.read()

    def _record_until_silence(self) -> np.ndarray:
        print("[STT] Listening... speak when ready.")

        audio_queue: "queue.Queue[np.ndarray]" = queue.Queue()
        stop_event = threading.Event()

        frame_samples = int(SAMPLE_RATE * FRAME_DURATION_SEC)
        max_frames = int(RECORD_MAX_SECONDS / FRAME_DURATION_SEC)
        min_frames = int(MIN_RECORD_SECONDS / FRAME_DURATION_SEC)
        silence_frames_required = int(SILENCE_DURATION_SEC / FRAME_DURATION_SEC)

        pre_roll_frames = max(1, int(PRE_ROLL_SEC / FRAME_DURATION_SEC))
        pre_roll: Deque[np.ndarray] = deque(maxlen=pre_roll_frames)

        state = {
            "frames": [],
            "recording": False,
            "speech_hits": 0,
            "silence_hits": 0,
            "total_frames": 0,
            "dead_frames": 0,
        }

        def callback(indata, frames, time_info, status):
            if status:
                print(f"[STT] Recording status: {status}", file=sys.stderr)
            if stop_event.is_set():
                return
            data = indata.copy().reshape(-1).astype("float32")
            audio_queue.put(data)

        stream_kwargs = dict(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            blocksize=frame_samples,
            callback=callback,
        )
        if INPUT_DEVICE is not None:
            stream_kwargs["device"] = INPUT_DEVICE

        with sd.InputStream(**stream_kwargs):
            # ---- Flush any buffered audio (esp. after TTS playback) ----
            flush_until = int(0.25 / FRAME_DURATION_SEC)  # 250ms flush
            for _ in range(flush_until):
                try:
                    audio_queue.get_nowait()
                except queue.Empty:
                    break

            # ---- Calibration: estimate noise floor robustly ----
            calib_frames = max(1, int(CALIBRATION_SEC / FRAME_DURATION_SEC))
            noise_vals: List[float] = []
            for _ in range(calib_frames):
                try:
                    f = audio_queue.get(timeout=1.0)
                except queue.Empty:
                    continue
                if self._is_dead(f):
                    state["dead_frames"] += 1
                    continue
                r = self._rms(f)
                # ignore accidental speech spikes
                if r < 0.20:
                    noise_vals.append(r)

            if state["dead_frames"] >= int(calib_frames * 0.7):
                print("[STT] ⚠️ Microphone looks silent (dead frames). Wrong device or mic permission issue.")
                return np.zeros(0, dtype="float32")

            noise_rms = float(np.median(noise_vals)) if noise_vals else 0.002

            start_thresh = max(MIN_START_THRESH, min(MAX_START_THRESH, noise_rms * START_MULTIPLIER))
            stop_thresh = max(MIN_START_THRESH, min(MAX_START_THRESH, noise_rms * STOP_MULTIPLIER))

            print(
                f"[STT] Calibrated noise_rms={noise_rms:.5f} "
                f"start_thresh={start_thresh:.5f} stop_thresh={stop_thresh:.5f} "
                f"(start_frames={START_FRAMES_REQUIRED}, silence_frames_required={silence_frames_required})"
            )

            # ---- Wait for speech start ----
            wait_frames = int(WAIT_FOR_SPEECH_SECONDS / FRAME_DURATION_SEC)
            for _ in range(wait_frames):
                try:
                    f = audio_queue.get(timeout=1.0)
                except queue.Empty:
                    continue

                if self._is_dead(f):
                    state["dead_frames"] += 1
                    continue

                pre_roll.append(f)
                r = self._rms(f)

                if r >= start_thresh:
                    state["speech_hits"] += 1
                else:
                    state["speech_hits"] = 0

                if state["speech_hits"] >= START_FRAMES_REQUIRED:
                    state["recording"] = True
                    state["frames"].extend(list(pre_roll))
                    print("[STT] Speech detected, recording started.")
                    break

            if not state["recording"]:
                print("[STT] Reached max wait time, no speech detected.")
                return np.zeros(0, dtype="float32")

            # ---- Record until trailing silence or hard cap ----
            while True:
                try:
                    f = audio_queue.get(timeout=1.0)
                except queue.Empty:
                    break

                if self._is_dead(f):
                    state["dead_frames"] += 1
                    # if device dies mid-recording, break early
                    if state["dead_frames"] > 50:
                        print("[STT] ⚠️ Microphone became silent mid-recording.")
                        break
                    continue

                state["frames"].append(f)
                state["total_frames"] += 1

                r = self._rms(f)

                if state["total_frames"] >= min_frames:
                    if r < stop_thresh:
                        state["silence_hits"] += 1
                    else:
                        state["silence_hits"] = 0

                    if state["silence_hits"] >= silence_frames_required:
                        print("[STT] Detected pause, stopping.")
                        break

                if state["total_frames"] >= max_frames:
                    print("[STT] Reached max duration, stopping.")
                    break

            stop_event.set()

        if not state["frames"]:
            return np.zeros(0, dtype="float32")

        return np.concatenate(state["frames"]).astype("float32")

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