import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from openai import OpenAI


class OpenAITTS:
    """
    Reliable TTS playback on macOS.

    - Generates WAV via OpenAI
    - Plays via macOS `afplay` (ships with macOS)
    - Uses temp files by default to avoid leaving artifacts on disk
    """

    def __init__(
        self,
        model: Optional[str] = None,
        voice: Optional[str] = None,
    ):
        self.client = OpenAI()
        self.model = model or os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
        self.voice = voice or os.getenv("OPENAI_TTS_VOICE", "alloy")

        # If set to "1", we keep the wav files (otherwise temp files are deleted)
        self.keep_files = os.getenv("KEEP_TTS_WAV", "0").strip() == "1"

        # Optional: where to store kept files
        self.out_dir = Path(os.getenv("TTS_OUT_DIR", "tts_out")).resolve()
        if self.keep_files:
            self.out_dir.mkdir(parents=True, exist_ok=True)

    def speak(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return

        wav_path = self._synthesize_to_wav(text)

        # Play on macOS
        try:
            # afplay blocks until audio finishes -> good for “call style” turn-taking
            subprocess.run(["afplay", str(wav_path)], check=False)
        finally:
            # Cleanup temp file unless KEEP_TTS_WAV=1
            if not self.keep_files:
                try:
                    wav_path.unlink(missing_ok=True)
                except Exception:
                    pass

    def _synthesize_to_wav(self, text: str) -> Path:
        if self.keep_files:
            fd, path = tempfile.mkstemp(prefix="tts_", suffix=".wav", dir=str(self.out_dir))
        else:
            fd, path = tempfile.mkstemp(prefix="tts_", suffix=".wav")
        os.close(fd)
        wav_path = Path(path)

        # ❌ DO NOT pass format="wav"
        response = self.client.audio.speech.create(
            model=self.model,
            voice=self.voice,
            input=text,
        )

        # Handle all SDK variants safely
        if hasattr(response, "write_to_file"):
            response.write_to_file(str(wav_path))
        else:
            data = None
            if hasattr(response, "read"):
                data = response.read()
            elif hasattr(response, "content"):
                data = response.content
            else:
                data = bytes(response)

            with open(wav_path, "wb") as f:
                f.write(data)

        return wav_path