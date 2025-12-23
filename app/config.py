import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is not set in environment or .env")

# ============================================================
# AUDIO CONFIG
# ============================================================
SAMPLE_RATE = 16000
CHANNELS = 1

# Frame-based recording
FRAME_DURATION_SEC = 0.03  # 30ms frames

# ============================================================
# RECORDING LIMITS
# ============================================================
RECORD_MAX_SECONDS = 15

# Legacy fallback
SILENCE_THRESHOLD = 0.015
SILENCE_DURATION_SEC = 1.0

# ============================================================
# ADVANCED VAD (noise robust)
# ============================================================
VAD_CALIBRATION_SEC = 0.6
VAD_START_FRAMES = 6
VAD_PRE_ROLL_SEC = 0.25
VAD_MIN_RECORD_SEC = 0.5

VAD_START_MULTIPLIER = 3.0
VAD_STOP_MULTIPLIER = 2.0

VAD_ABS_START_FLOOR = 0.012
VAD_ABS_STOP_FLOOR = 0.010

# ============================================================
# OPENAI MODELS (cheap baseline)
# ============================================================
STT_MODEL = "gpt-4o-mini-transcribe"
TTS_MODEL = "gpt-4o-mini-tts"
NLU_MODEL = "gpt-4o-mini"
DIALOG_MODEL = "gpt-4o-mini"

# ============================================================
# DATABASE (MYSQL)
# ============================================================
MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DB = os.getenv("MYSQL_DB", "voice_facilitator")

# ============================================================
# SYSTEM BEHAVIOR
# ============================================================
INTENT_CONFIDENCE_FALLBACK = 0.4
DEBUG_MODE = True