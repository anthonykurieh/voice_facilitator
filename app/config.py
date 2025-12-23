import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is not set in environment or .env")

# Audio
SAMPLE_RATE = 16000
CHANNELS = 1

# If None -> default input device
# Set this to the index you see from: python -c "import sounddevice as sd; print(sd.query_devices())"
INPUT_DEVICE = os.getenv("INPUT_DEVICE")
INPUT_DEVICE = int(INPUT_DEVICE) if INPUT_DEVICE not in (None, "", "None") else None

# STT Model
STT_MODEL = "gpt-4o-mini-transcribe"

# --- Voice Activity Detection / Recording behavior ---
WAIT_FOR_SPEECH_SECONDS = 10.0        # wait longer for user to begin speaking
RECORD_MAX_SECONDS = 15.0             # hard cap for a turn
FRAME_DURATION_SEC = 0.03             # 30ms frames
CALIBRATION_SEC = 0.8                 # longer calibration
PRE_ROLL_SEC = 0.35                   # keep more audio before speech start (prevents clipping)

MIN_RECORD_SECONDS = 0.45
SILENCE_DURATION_SEC = 1.0

# Adaptive thresholds
MIN_START_THRESH = 0.008              # easier to trigger
MAX_START_THRESH = 0.050
START_MULTIPLIER = 4.5                # lower => easier start detection
STOP_MULTIPLIER = 3.0

START_FRAMES_REQUIRED = 4             # fewer consecutive frames needed to start recording

# TTS Model
TTS_MODEL = "gpt-4o-mini-tts"

# Dialog
NLU_MODEL = "gpt-4o-mini"
DIALOG_MODEL = "gpt-4o-mini"