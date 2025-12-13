import io
import queue
import sys
import threading
from typing import Optional

import numpy as np
import sounddevice as sd
import soundfile as sf

from app.pipeline.openai_client import client
from app.config import (
    SAMPLE_RATE,
    CHANNELS,
    RECORD_MAX_SECONDS,
    SILENCE_THRESHOLD,
    SILENCE_DURATION_SEC,
    FRAME_DURATION_SEC,
    STT_MODEL,
)


class OpenAISTT:
    """
    STT wrapper that:
    - Records from the default microphone
    - Stops when the user is silent for a bit OR max duration is reached
    - Sends the audio to OpenAI STT and returns the transcript
    """

    def __init__(self, model: str = STT_MODEL):
        self.model = model

    def _record_until_silence(self) -> np.ndarray:
        """
        Record audio until we detect enough continuous silence or hit max time.
        Returns a 1D NumPy float32 array.
        """
        print("[STT] Speak now. Recording will stop after a pause or max duration.")

        audio_queue: "queue.Queue[np.ndarray]" = queue.Queue()
        stop_event = threading.Event()

        frame_samples = int(SAMPLE_RATE * FRAME_DURATION_SEC)
        max_frames = int(RECORD_MAX_SECONDS / FRAME_DURATION_SEC)
        silence_frames_required = int(SILENCE_DURATION_SEC / FRAME_DURATION_SEC)

        state = {
            "frames": [],
            "consecutive_silence": 0,
            "total_frames": 0,
        }

        def callback(indata, frames, time, status):
            if status:
                print(f"[STT] Recording status: {status}", file=sys.stderr)

            if stop_event.is_set():
                return

            # mono as float32
            data = indata.copy()
            audio_queue.put(data)

        # Start stream
        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            blocksize=frame_samples,
            callback=callback,
        ):
            while True:
                try:
                    frame = audio_queue.get(timeout=RECORD_MAX_SECONDS)
                except queue.Empty:
                    # No frames received; stop
                    print("[STT] No audio received, stopping.")
                    break

                state["frames"].append(frame)
                state["total_frames"] += 1

                # Compute RMS energy for silence detection
                rms = np.sqrt(np.mean(np.square(frame), axis=0)).mean()

                if rms < SILENCE_THRESHOLD:
                    state["consecutive_silence"] += 1
                else:
                    state["consecutive_silence"] = 0

                # Check stopping conditions
                if state["consecutive_silence"] >= silence_frames_required:
                    # Enough silence -> stop recording
                    print("[STT] Detected pause in speech, stopping recording.")
                    break

                if state["total_frames"] >= max_frames:
                    print("[STT] Reached maximum recording duration, stopping.")
                    break

            stop_event.set()

        if not state["frames"]:
            return np.zeros(0, dtype="float32")

        audio = np.concatenate(state["frames"], axis=0)

        # Flatten if stereo (should be mono anyway)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)

        return audio.astype("float32")

    def _audio_to_wav_bytes(self, audio: np.ndarray) -> bytes:
        """
        Convert a float32 np array to WAV bytes in memory.
        """
        buf = io.BytesIO()
        sf.write(buf, audio, SAMPLE_RATE, format="WAV", subtype="PCM_16")
        buf.seek(0)
        return buf.read()

    def record_and_transcribe(self) -> str:
        """
        Main entrypoint:
        - Record from mic until silence / timeout
        - Transcribe with OpenAI
        """
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