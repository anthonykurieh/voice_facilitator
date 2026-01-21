# Analytics Schema Context

Use this schema for generating SELECT-only SQL. Never write/modify data.

## Tables

### customers
- id (int, PK)
- phone (varchar)
- name (varchar)
- email (varchar)
- created_at (timestamp)

### services
- id (int, PK)
- business_id (int)
- name (varchar)
- duration_minutes (int)
- price (decimal)
- active (bool)

### staff
- id (int, PK)
- business_id (int)
- name (varchar)
- available (bool)

### appointments
- id (int, PK)
- business_id (int)
- customer_id (int)
- staff_id (int)
- service_id (int)
- appointment_date (date)
- appointment_time (time)
- duration_minutes (int)
- status (varchar)  -- scheduled, completed, cancelled, no_show
- created_at (timestamp)

### business_hours
- id (int, PK)
- business_id (int)
- day_of_week (int)  -- 0=Mon
- open_time (time)
- close_time (time)
- is_closed (bool)

### calls
- id (int, PK)
- business_id (int)
- customer_id (int)
- started_at (timestamp)
- ended_at (timestamp)
- outcome (varchar) -- booked, cancelled, inquiry, no_answer
- transcript (text)

### kpi_events
- id (int, PK)
- appointment_id (int)
- event_type (varchar) -- booked, cancelled, reschedule_new, reschedule_cancel_old
- service_id (int)
- service_name (varchar)
- service_price (decimal)
- staff_id (int)
- staff_name (varchar)
- duration_minutes (int)
- status (varchar)
- appointment_date (date)
- appointment_time (time)
- created_at (timestamp)

## Common metrics examples
- "How many customers last 7 days?" -> COUNT(DISTINCT customer_id) from appointments where appointment_date >= CURDATE()-INTERVAL 6 DAY and status IN ('scheduled','completed')
- "Revenue last month" -> SUM(service_price) from kpi_events where event_type='booked' and created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
- "No-show rate" -> no_show / total from appointments grouped by date
- "Service mix" -> COUNT(*) grouped by service_name from kpi_events where event_type='booked'
- "Staff load" -> SUM(duration_minutes) grouped by staff_name from kpi_events where event_type='booked'
- "Calls that converted" -> count calls outcome='booked' grouped by day

## Rules for SQL generation
- SELECT-only. No INSERT/UPDATE/DELETE/ALTER/DROP.
- Use only listed tables/columns.
- Apply LIMIT 200 for raw row listings.
- Prefer date filters relative to CURDATE().
- Use COALESCE for nullable names if needed.
- Never expose PII (phone/email) in summaries; redact if present.
