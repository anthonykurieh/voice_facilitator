# Fixing PyAudio Installation on macOS

## The Problem

PyAudio failed to build because it requires the PortAudio library, which isn't installed on your system.

## Solution: Install PortAudio

### Option 1: Using Homebrew (Recommended)

```bash
# Install PortAudio
brew install portaudio

# Then retry installing requirements
pip install -r requirements.txt
```

### Option 2: Using Conda

```bash
# Install PortAudio via conda
conda install -c conda-forge portaudio

# Then install PyAudio
pip install pyaudio
```

### Option 3: Install PyAudio Only (if PortAudio is already installed)

```bash
pip install pyaudio
```

## Verify Installation

After installing, test if PyAudio works:

```bash
python -c "import pyaudio; print('PyAudio installed successfully!')"
```

## Alternative: Skip PyAudio for Now

If you want to proceed without audio input for now, you can comment out PyAudio in requirements.txt and install the rest:

```bash
# Edit requirements.txt and comment out pyaudio line
# Then install
pip install -r requirements.txt
```

Note: Without PyAudio, the STT (Speech-to-Text) module won't work, but you can test other parts of the system.

