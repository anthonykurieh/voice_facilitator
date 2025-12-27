# Installation Guide

## The Issue

You're using `/usr/local/bin/python3` (system Python) but packages are installed in your conda environment.

## Solution: Use Conda Python

### Option 1: Use Conda Python Directly

Instead of:
```bash
/usr/local/bin/python3 src/init_database.py
```

Use:
```bash
python src/init_database.py
# or
python -m src.init_database
```

This will use the conda Python where your packages are installed.

### Option 2: Install All Requirements

Install all dependencies in your current environment:

```bash
pip install -r requirements.txt
```

This installs:
- `python-dotenv` (not `dotenv`)
- `pymysql`
- `pyyaml`
- `openai`
- `pyaudio`
- `numpy`
- `pydub`
- `simpleaudio`
- `python-dateutil`
- `pytz`

### Option 3: Use Python Module Syntax

Run as a module (recommended):

```bash
python -m src.init_database
```

This ensures Python finds all modules correctly.

## Quick Fix

Run this in your terminal:

```bash
# Make sure you're in the project directory
cd "/Users/anthony/PycharmProjects/Voice Facilitator"

# Install all requirements
pip install -r requirements.txt

# Run the init script using Python module syntax
python -m src.init_database
```

## Verify Installation

Check if packages are installed:

```bash
python -c "import dotenv; import pymysql; import yaml; print('All packages OK!')"
```

If this works, you're good to go!

