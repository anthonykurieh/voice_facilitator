import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is not set in environment or .env")


# ================
# AUDIO CONFIG
# ================

SAMPLE_RATE = 16000
CHANNELS = 1

# Maximum amount of time the user can speak before hard stop
RECORD_MAX_SECONDS = 15

# Silence detection (for "record until pause")
SILENCE_THRESHOLD = 0.015     # Lower = more sensitive to quiet speech
SILENCE_DURATION_SEC = 1.0    # Need 1 second of silence to stop recording
FRAME_DURATION_SEC = 0.05     # 50 ms audio chunks


# ================
# MODEL CONFIG
# ================

# STT
STT_MODEL = "gpt-4o-mini-transcribe"   # whisper-large-v3 backend

# TTS
TTS_MODEL = "gpt-4o-mini-tts"

# NLU classifier
NLU_MODEL = "gpt-4o-mini"

# Full dialog model
DIALOG_MODEL = "gpt-4o-mini"