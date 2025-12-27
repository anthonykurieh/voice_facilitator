# How to Run the Voice Assistant

## The Problem

If you see `ModuleNotFoundError: No module named 'openai'`, you're using the wrong Python interpreter.

## Solution: Use Conda Python

**Don't use:**
```bash
/usr/local/bin/python3 main.py  # ❌ System Python (no packages)
```

**Use instead:**
```bash
python main.py                  # ✅ Conda Python (has packages)
# or
python -m src.voice_loop        # ✅ Also works
```

## Quick Check

Check which Python has the packages:

```bash
# This should work (conda Python)
python -c "import openai; print('OK')"

# This might fail (system Python)
/usr/local/bin/python3 -c "import openai; print('OK')"
```

## In PyCharm

If running from PyCharm:
1. Go to Run → Edit Configurations
2. Make sure Python interpreter is set to your conda environment
3. Should show something like: `/Users/anthony/anaconda3/bin/python`

## Full Setup Sequence

```bash
# 1. Make sure you're in the project directory
cd "/Users/anthony/PycharmProjects/Voice Facilitator"

# 2. Install dependencies (if not already done)
pip install -r requirements.txt

# 3. Initialize database
python -m src.init_database

# 4. Run the assistant
python main.py
```

## Verify Your Setup

```bash
# Check Python location
which python

# Should show: /Users/anthony/anaconda3/bin/python

# Test imports
python -c "import openai, pymysql, yaml, sounddevice; print('All packages OK!')"
```

If all checks pass, you're ready to run!

