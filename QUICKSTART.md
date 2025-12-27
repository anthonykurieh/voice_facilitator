# Quick Start Guide

Get the Voice Assistant running in 5 minutes.

## Prerequisites Check

- [ ] Python 3.8+ installed
- [ ] MySQL server running
- [ ] OpenAI API key ready
- [ ] Microphone connected

## Step-by-Step Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment

Create `.env` file:

```bash
OPENAI_API_KEY=sk-your-key-here
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=yourpassword
MYSQL_DATABASE=voice_assistant
CONFIG_FILE=config/business_config.yaml
```

### 3. Create Business Config

```bash
cp config/business_config.yaml.example config/business_config.yaml
```

Edit `config/business_config.yaml` with your business details.

### 4. Initialize Database

```bash
python -m src.init_database
```

### 5. Run!

```bash
python main.py
```

## First Run

1. System calibrates noise (be quiet for 2 seconds)
2. Assistant greets you
3. Start speaking naturally!

## Example Conversation

**You:** "I'd like to book a haircut"

**Assistant:** "I'd be happy to help you book a haircut. When would you like to come in?"

**You:** "Tomorrow afternoon"

**Assistant:** "Let me check availability for tomorrow afternoon... I have slots at 2:00 PM and 3:30 PM. Which works better?"

**You:** "2 PM works"

**Assistant:** "Perfect! I have you down for a haircut tomorrow at 2:00 PM. Can I get your name and phone number?"

## Troubleshooting

**"OPENAI_API_KEY not found"**
- Check your `.env` file exists and has the correct key

**"MySQL connection failed"**
- Verify MySQL is running: `mysql -u root -p`
- Check credentials in `.env`

**"Config file not found"**
- Make sure `config/business_config.yaml` exists (copy from `.example`)

**No audio output**
- Check system volume
- Verify microphone permissions (macOS: System Preferences > Security)

**Poor transcription**
- Speak clearly in a quiet environment
- Check microphone is working

## Next Steps

- Customize `config/business_config.yaml` for your business
- Adjust personality and tone
- Add more services or staff
- Modify business hours

That's it! You're ready to go.

