"""Send daily appointment summaries to staff via email (HTML).

Usage:
    python send_daily_staff_emails.py

Env vars required:
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM
Optional:
    SMTP_TLS=true|false (default true)
    DRY_RUN=true|false (default false)
"""
import os
import sys
import smtplib
from datetime import datetime, date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

from src.config_loader import ConfigLoader
from src.database import Database
from src.config import APP_TIMEZONE


def _get_env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y"}


def _get_timezone(config: ConfigLoader) -> str:
    return config.get("business.timezone", APP_TIMEZONE) or APP_TIMEZONE


def _today_in_tz(tz_name: str) -> date:
    try:
        return datetime.now(ZoneInfo(tz_name)).date()
    except Exception:
        return datetime.now().date()


def _load_staff_emails(config: ConfigLoader) -> dict:
    staff = config.get_staff()
    staff_map = {}
    for member in staff:
        name = (member.get("name") or "").strip()
        email = (member.get("email") or "").strip()
        if name and email:
            staff_map[name] = email
    return staff_map


def _fetch_appointments_for_date(db: Database, appt_date: date):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT a.id, a.appointment_date, a.appointment_time, a.duration_minutes, a.status,
                   s.name AS service_name, st.name AS staff_name, c.name AS customer_name, c.phone AS customer_phone
            FROM appointments a
            LEFT JOIN services s ON a.service_id = s.id
            LEFT JOIN staff st ON a.staff_id = st.id
            LEFT JOIN customers c ON a.customer_id = c.id
            WHERE a.appointment_date = %s AND a.status = 'scheduled'
            ORDER BY st.name, a.appointment_time
            """,
            (appt_date,),
        )
        return cursor.fetchall()


def _format_time(value) -> str:
    if value is None:
        return "TBD"
    try:
        return value.strftime("%I:%M %p").lstrip("0")
    except Exception:
        return str(value)


def _build_email_html(business_name: str, staff_name: str, appt_date: date, rows: list) -> str:
    date_label = appt_date.strftime("%A, %B %d, %Y")
    header = f"""
    <div style="font-family: Arial, sans-serif; color: #111;">
      <h2 style="margin: 0 0 6px 0;">{business_name} • Daily Schedule</h2>
      <div style="color:#555; margin-bottom: 16px;">{staff_name} — {date_label}</div>
    """
    if not rows:
        body = """
        <div style="padding: 12px 14px; background:#f7f7f7; border-radius: 8px;">
          No appointments scheduled for today.
        </div>
        """
        return header + body + "</div>"

    table_rows = []
    for r in rows:
        table_rows.append(
            f"""
            <tr>
              <td style="padding:8px 10px; border-bottom:1px solid #eee;">{_format_time(r.get('appointment_time'))}</td>
              <td style="padding:8px 10px; border-bottom:1px solid #eee;">{r.get('service_name') or 'Service'}</td>
              <td style="padding:8px 10px; border-bottom:1px solid #eee;">{r.get('customer_name') or 'Customer'}</td>
              <td style="padding:8px 10px; border-bottom:1px solid #eee;">{r.get('duration_minutes') or 30} min</td>
            </tr>
            """
        )
    body = f"""
    <table style="border-collapse: collapse; width:100%; font-size: 14px;">
      <thead>
        <tr>
          <th align="left" style="padding:8px 10px; border-bottom:2px solid #111;">Time</th>
          <th align="left" style="padding:8px 10px; border-bottom:2px solid #111;">Service</th>
          <th align="left" style="padding:8px 10px; border-bottom:2px solid #111;">Customer</th>
          <th align="left" style="padding:8px 10px; border-bottom:2px solid #111;">Duration</th>
        </tr>
      </thead>
      <tbody>
        {''.join(table_rows)}
      </tbody>
    </table>
    """
    return header + body + "</div>"


def _send_email(smtp_host: str, smtp_port: int, smtp_user: str, smtp_password: str,
                smtp_from: str, to_email: str, subject: str, html_body: str, use_tls: bool):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        if use_tls:
            server.starttls()
        if smtp_user and smtp_password:
            server.login(smtp_user, smtp_password)
        server.sendmail(smtp_from, [to_email], msg.as_string())


def main():
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    smtp_from = os.getenv("SMTP_FROM")
    use_tls = _get_env_bool("SMTP_TLS", True)
    dry_run = _get_env_bool("DRY_RUN", False)

    if not smtp_host or not smtp_from:
        print("Missing SMTP_HOST or SMTP_FROM env vars.")
        sys.exit(1)

    config_path = os.getenv("CONFIG_FILE", "config/business_config.yaml")
    config = ConfigLoader(config_path)
    db = Database()

    tz_name = _get_timezone(config)
    today = _today_in_tz(tz_name)
    business_name = config.get_business_name()
    staff_emails = _load_staff_emails(config)

    if not staff_emails:
        print("No staff emails found in config.")
        return

    appointments = _fetch_appointments_for_date(db, today)
    staff_groups = {}
    for row in appointments:
        staff_name = row.get("staff_name") or "Unassigned"
        staff_groups.setdefault(staff_name, []).append(row)

    for staff_name, email in staff_emails.items():
        rows = staff_groups.get(staff_name, [])
        subject = f"{business_name} schedule for {today.strftime('%b %d, %Y')}"
        html_body = _build_email_html(business_name, staff_name, today, rows)
        if dry_run:
            print(f"[DRY_RUN] Would send to {email}: {len(rows)} appointments")
            continue
        _send_email(
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_user=smtp_user,
            smtp_password=smtp_password,
            smtp_from=smtp_from,
            to_email=email,
            subject=subject,
            html_body=html_body,
            use_tls=use_tls,
        )
        print(f"Sent to {email}: {len(rows)} appointments")


if __name__ == "__main__":
    main()
