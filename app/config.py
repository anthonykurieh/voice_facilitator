import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is not set in environment or .env")

# ---- App timezone (used for "today/tomorrow/next monday") ----
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Beirut")

# ---- Audio config ----
SAMPLE_RATE = 16000
CHANNELS = 1

# Record-until-silence settings
RECORD_MAX_SECONDS = 15          # hard cap per user turn
FRAME_DURATION_SEC = 0.03        # 30ms frames
SILENCE_DURATION_SEC = 1.0       # stop after 1s trailing silence

# Energy thresholds (these are "base" bounds; STT calibrates per call)
ENERGY_FLOOR = 0.010             # minimum start threshold
ENERGY_CEIL = 0.050              # maximum start threshold
STOP_THRESHOLD_RATIO = 0.70      # stop threshold = start_thresh * ratio

# ---- Models ----
STT_MODEL = os.getenv("STT_MODEL", "gpt-4o-mini-transcribe")
TTS_MODEL = os.getenv("TTS_MODEL", "gpt-4o-mini-tts")
DIALOG_MODEL = os.getenv("DIALOG_MODEL", "gpt-4o-mini")