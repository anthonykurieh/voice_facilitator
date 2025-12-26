# app/pipeline/tts_openai.py
from __future__ import annotations

import os
import time
from openai import OpenAI


class OpenAITTS:
    def __init__(self, model: str = "gpt-4o-mini-tts", voice: str = "alloy"):
        self.client = OpenAI()
        self.model = model
        self.voice = voice

    def synthesize_to_wav(self, text: str) -> str:
        # unique filename
        out_path = os.path.join(os.path.dirname(__file__), f"_tts_{int(time.time()*1000)}.wav")

        # Important: set a per-request timeout via httpx client config if you want hard timeouts.
        # For now we keep it minimal and rely on user interrupt, but you can configure http_client in OpenAI() too.
        response = self.client.audio.speech.create(
            model=self.model,
            voice=self.voice,
            input=text,
        )

        response.write_to_file(out_path)
        return out_path

    def speak(self, text: str) -> None:
        # keep your existing playback logic here
        wav_path = self.synthesize_to_wav(text)
        print("[TTS] Playing response...")
        # ... your existing local playback ...
        print("[TTS] Playback finished.")