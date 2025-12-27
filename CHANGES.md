# Configuration Updates

The system has been updated to use your environment variable names and audio configuration.

## Environment Variables

Your `.env` file uses these variable names (which are now supported):

- `OPENAI_API_KEY` - OpenAI API key
- `DB_HOST` - Database host (was `MYSQL_HOST`)
- `DB_PORT` - Database port (was `MYSQL_PORT`)
- `DB_USER` - Database user (was `MYSQL_USER`)
- `DB_PASSWORD` - Database password (was `MYSQL_PASSWORD`)
- `DB_NAME` - Database name (was `MYSQL_DATABASE`)

The system now supports both naming conventions for backward compatibility.

## New Configuration Options

You can now add these optional environment variables to your `.env`:

```bash
# Timezone (default: Asia/Beirut)
APP_TIMEZONE=Asia/Beirut

# Audio settings
SAMPLE_RATE=16000
CHANNELS=1
RECORD_MAX_SECONDS=15
FRAME_DURATION_SEC=0.03
SILENCE_DURATION_SEC=1.0
ENERGY_FLOOR=0.010
ENERGY_CEIL=0.050
STOP_THRESHOLD_RATIO=0.70

# Model selection
STT_MODEL=whisper-1
TTS_MODEL=tts-1
DIALOG_MODEL=gpt-4o
```

## Improvements

1. **Better Audio Detection**: Uses energy-based thresholds with calibration
   - Separate start and stop thresholds
   - Automatic calibration per call
   - Configurable energy floor/ceiling

2. **Timezone Support**: Date parsing now respects `APP_TIMEZONE`
   - "today", "tomorrow", "next Monday" use the configured timezone

3. **Model Configuration**: All models configurable via environment variables
   - STT model (default: whisper-1)
   - TTS model (default: tts-1)
   - Dialog model (default: gpt-4o)

4. **Centralized Config**: New `src/config.py` module centralizes all configuration
   - Single source of truth for environment variables
   - Type-safe defaults

## Updated Files

- `src/database.py` - Now uses `DB_*` variable names
- `src/stt.py` - Improved audio detection with energy thresholds
- `src/tts.py` - Uses `TTS_MODEL` from config
- `src/agent.py` - Uses `DIALOG_MODEL` from config
- `src/tools.py` - Timezone-aware date parsing
- `src/config.py` - New centralized configuration module
- `requirements.txt` - Added `pytz` for timezone support

## Usage

No changes needed to your existing `.env` file! The system will automatically:
- Use your `DB_*` variable names
- Apply your audio configuration (if specified)
- Use timezone-aware date parsing

If you want to customize audio or model settings, just add them to your `.env` file.

