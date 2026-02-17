# AI Voice Assistant

An agentic, business-agnostic AI voice assistant that answers phone calls for any business. This is a voice-first system powered by LLM orchestration, not a traditional rules-based IVR.

## Core Philosophy

- **Agentic**: The LLM is the brain that decides what's missing, plans next steps, and chooses when to call backend actions
- **Business-Agnostic**: All business-specific information lives in a single config file
- **Config-Driven**: Change one config file → restart → works for any business
- **LLM-Orchestrated**: No rigid conversation flows or hardcoded logic
- **Robust**: Handles messy, incomplete, or grammatically incorrect speech naturally

## Architecture

The system operates in a voice-native loop:

1. **Listen** (STT) - Real-time microphone input with silence detection
2. **Think** (LLM Agent) - Processes input, makes decisions, plans actions
3. **Act** (Backend Tools) - Executes database operations and business logic
4. **Speak** (TTS) - Natural voice output

This loop repeats until the call ends.

## Features

- Natural conversation flow with state tracking
- Handles interruptions and corrections gracefully
- Understands natural language dates/times ("tomorrow", "this Saturday", "around 10")
- Books, cancels, and reschedules appointments
- Checks availability and suggests alternatives
- Remembers context across turns
- No infinite clarification loops
- Multi-business config support (switch profiles via GUI)
- Visual Business Config Builder (HTML form → YAML file)
- Staff daily email summaries with per-business theme colors

## Setup

### Prerequisites

- Python 3.8+
- MySQL server
- OpenAI API key
- Microphone access

### Installation

1. **Clone and install dependencies:**

```bash
pip install -r requirements.txt
```

2. **Set up environment variables:**

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

Edit `.env`:
```
OPENAI_API_KEY=your_openai_api_key_here
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your_password_here
MYSQL_DATABASE=voice_assistant
CONFIG_FILE=config/business_config.yaml
```

3. **Create business configuration:**

Copy the example config and customize:

```bash
cp config/business_config.yaml.example config/business_config.yaml
```

Edit `config/business_config.yaml` with your business details:
- Business name and type
- Services offered
- Staff members
- Business hours
- Booking rules
- Personality/tone

4. **Initialize database:**

First, make sure MySQL is running and the database exists (or the user has permission to create it).

Then initialize the database with your business data:

```bash
python -m src.init_database
```

This will:
- Create all necessary tables
- Insert your business information from the config file
- Set up services, staff, and business hours

5. **Run the assistant:**

```bash
python main.py
```

## Configuration

All business-specific information is in `config/business_config.yaml`:

- **business**: Name, type, phone
- **personality**: Tone, greeting template
- **services**: List of services with duration and price
- **staff**: Available staff members
- **hours**: Opening hours for each day
- **booking**: Booking rules (advance notice, buffers, etc.)

To switch businesses, simply change the config file and restart.

### Business Config Builder (GUI + HTML)

- Open `python demo_launcher.py`
- Go to **Admin Mode** → **Business Config Builder**
- A local page opens at `http://127.0.0.1:8765`
- Fill the form and click **Create Config File**
- New files are created under `config/` as `business_config_<name>.yaml`
- The launcher auto-detects new config files

### Daily Staff Emails

Set SMTP values in `.env`:

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your_sender@gmail.com
SMTP_PASSWORD=your_app_password
SMTP_FROM=your_sender@gmail.com
SMTP_TLS=true
```

Send from GUI:
- **Admin Mode** → **Send Daily Staff Emails**
- The button runs a built-in dry-run preview first, then asks for confirmation before real send.

Optional manual run:

```bash
CONFIG_FILE=config/business_config_riverstone.yaml python send_daily_staff_emails.py
```

Optional date override for preview/testing:

```bash
EMAIL_DATE_OVERRIDE=2026-02-18 CONFIG_FILE=config/business_config_riverstone.yaml python send_daily_staff_emails.py
```

## Usage

1. Start the assistant: `python main.py`
2. The system will calibrate noise levels (be quiet for 2 seconds)
3. Speak naturally when prompted
4. The assistant will:
   - Listen to your speech
   - Process it with the LLM
   - Execute any needed actions (check availability, book appointments, etc.)
   - Respond naturally
5. Press Ctrl+C to end the call

## Project Structure

```
.
├── main.py                 # Entry point
├── src/
│   ├── __init__.py
│   ├── config_loader.py   # Business config loader
│   ├── database.py        # MySQL database layer
│   ├── stt.py             # Speech-to-Text
│   ├── tts.py             # Text-to-Speech
│   ├── agent.py           # LLM agent orchestrator
│   ├── tools.py           # Backend actions/tools
│   └── voice_loop.py      # Main conversation loop
├── config/
│   └── business_config.yaml.example
├── requirements.txt
└── README.md
```

## How It Works

### Speech-to-Text (STT)
- Real-time microphone listener
- Automatic silence detection
- Noise calibration
- Returns plain text only (no interpretation)

### Text-to-Speech (TTS)
- Converts agent decisions to natural speech
- Immediate playback
- No logic injection

### LLM Agent
- Maintains conversation state
- Decides what information is missing
- Plans next steps
- Chooses when to call backend actions
- Handles ambiguity and corrections
- Returns structured decisions (JSON)

### Backend Tools
- `check_availability`: Get available time slots
- `book_appointment`: Create new appointment
- `cancel_appointment`: Cancel existing appointment
- `get_customer_appointments`: List customer's appointments
- `reschedule_appointment`: Move appointment to new time
- `get_services`: List available services
- `get_staff`: List available staff

### Database
Automatically manages schema for:
- Businesses
- Customers
- Services
- Staff
- Business hours
- Appointments
- Call logs

## Customization

### Changing Business Type

Simply edit `config/business_config.yaml`:
- Change `business.type`
- Update `services` list
- Modify `staff` members
- Adjust `hours`
- Update `personality.tone` and `personality.greeting`

No code changes needed!

### Adding New Actions

1. Add action method to `src/tools.py`
2. Update system prompt in `src/agent.py` to include the new action
3. The agent will automatically learn to use it

### Adjusting Personality

Edit the `personality` section in the config:
- `tone`: Description of how to behave
- `greeting`: Template for initial greeting

## Troubleshooting

### Audio Issues

- **No microphone detected**: Check system audio permissions
- **Poor transcription**: Ensure quiet environment, speak clearly
- **No audio output**: Check system volume, verify TTS API access

### Database Issues

- **Connection failed**: Verify MySQL is running and credentials are correct
- **Schema errors**: The system auto-creates schema; check user permissions

### API Issues

- **OpenAI errors**: Verify API key is valid and has credits
- **Rate limits**: The system uses GPT-4o; ensure sufficient quota

## Development

The system is designed to be:
- **Extendable**: Add new tools/actions easily
- **Testable**: Each component is modular
- **Debuggable**: Clear logging and error messages

## License

MIT License - feel free to use and modify for your business.

## Notes

- This is a voice-first system, not optimized for text chat
- Requires stable internet connection for OpenAI API calls
- MySQL database must be accessible
- Supports multiple business profiles in one database (scoped by `business_id`)
