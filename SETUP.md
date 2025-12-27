# Setup Instructions

Follow these steps to get your Voice Assistant running:

## Step 1: Create Business Configuration

Copy the example config file and customize it for your business:

```bash
cp config/business_config.yaml.example config/business_config.yaml
```

Then edit `config/business_config.yaml` with your business details:
- Business name, type, phone
- Services offered
- Staff members
- Business hours
- Personality/tone

## Step 2: Create the Database

Make sure MySQL is running, then create the database:

```bash
mysql -u vf_app -p
```

Then in MySQL:
```sql
CREATE DATABASE IF NOT EXISTS voice_facilitator;
EXIT;
```

Or if you have permissions, the init script will create it automatically.

## Step 3: Initialize Database Schema and Data

Run the initialization script:

```bash
python -m src.init_database
```

This will:
- Create all necessary tables (businesses, customers, services, staff, hours, appointments, calls)
- Insert your business data from the config file
- Set up services, staff, and business hours

**Note:** If you see "Business data already exists", that's fine - it means the database was already initialized.

## Step 4: Run the Voice Assistant

```bash
python main.py
```

The system will:
1. Calibrate noise levels (be quiet for 2 seconds)
2. Greet you
3. Start listening for your voice

## Troubleshooting

### Database Connection Error
- Verify MySQL is running: `mysql -u vf_app -p`
- Check your `.env` file has correct credentials
- Make sure the database exists: `SHOW DATABASES;`

### Config File Not Found
- Make sure `config/business_config.yaml` exists (copy from `.example`)

### Permission Errors
- Make sure the database user has CREATE, INSERT, UPDATE, DELETE permissions
- Or create the database manually first

### Audio Issues
- Check microphone permissions (macOS: System Preferences > Security & Privacy)
- Ensure microphone is connected and working

## Quick Test

After setup, you can test with:
- "I'd like to book an appointment"
- "What are your hours?"
- "What services do you offer?"

The assistant should respond naturally and help you book appointments!

