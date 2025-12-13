from pathlib import Path
import tempfile

import sounddevice as sd
import soundfile as sf

from app.pipeline.openai_client import client
from app.config import TTS_MODEL

class OpenAITTS:
    def __init__(self, voice: str = "alloy"):
        self.voice = voice

    def synthesize_to_wav(self, text: str) -> Path:
        """
        Call OpenAI TTS (gpt-4o-mini-tts) and save the returned audio bytes to a WAV file.
        No 'format' argument anymore — API returns raw audio bytes.
        """
        tmp_dir = tempfile.gettempdir()
        out_path = Path(tmp_dir) / "vf_output.wav"

        # NEW CORRECT SIGNATURE — no format= argument
        response = client.audio.speech.create(
            model=TTS_MODEL,
            voice=self.voice,
            input=text,
        )

        audio_bytes = response.read()   # always use .read() per new API docs

        with open(out_path, "wb") as f:
            f.write(audio_bytes)

        return out_path

    def play_wav(self, path: Path) -> None:
        data, sr = sf.read(path, dtype="float32")
        print("[TTS] Playing response...")
        sd.play(data, sr)
        sd.wait()
        print("[TTS] Playback finished.")

    def speak(self, text: str) -> None:
        if not text:
            return
        wav_path = self.synthesize_to_wav(text)
        self.play_wav(wav_path)