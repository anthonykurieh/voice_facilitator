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


def _target_date(tz_name: str) -> date:
    override = (os.getenv("EMAIL_DATE_OVERRIDE") or "").strip()
    if override:
        try:
            return datetime.strptime(override, "%Y-%m-%d").date()
        except ValueError:
            print(f"Invalid EMAIL_DATE_OVERRIDE '{override}'. Expected YYYY-MM-DD. Falling back to today.")
    return _today_in_tz(tz_name)


def _load_staff_emails(config: ConfigLoader) -> dict:
    staff = config.get_staff()
    staff_map = {}
    for member in staff:
        name = (member.get("name") or "").strip()
        email = (member.get("email") or "").strip()
        if name and email:
            staff_map[name] = email
    return staff_map


def _fetch_appointments_for_date(db: Database, appt_date: date, business_id: int):
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
            WHERE a.business_id = %s AND a.appointment_date = %s AND a.status = 'scheduled'
            ORDER BY st.name, a.appointment_time
            """,
            (business_id, appt_date),
        )
        return cursor.fetchall()


def _resolve_business_id(db: Database, config: ConfigLoader) -> int:
    config_business_id = config.get("business.id")
    if config_business_id:
        try:
            return int(config_business_id)
        except (TypeError, ValueError):
            pass
    business_name = config.get_business_name()
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM businesses WHERE LOWER(name) = LOWER(%s) LIMIT 1",
            (business_name,),
        )
        row = cursor.fetchone()
        if row and row.get("id"):
            return int(row["id"])
    return 1


def _format_time(value) -> str:
    if value is None:
        return "TBD"
    try:
        return value.strftime("%I:%M %p").lstrip("0")
    except Exception:
        return str(value)


def _get_email_theme(config: ConfigLoader) -> dict:
    default_theme = {
        "palette_name": "Clean Slate",
        "hero_gradient_start": "#0f172a",
        "hero_gradient_end": "#1d4ed8",
        "accent": "#2563eb",
        "accent_soft": "#dbeafe",
        "table_header_bg": "#f1f5f9",
        "table_header_text": "#0f172a",
        "panel_bg": "#f8fafc",
        "border": "#e2e8f0",
    }
    cfg_theme = config.get("email_theme", {}) or {}
    if not isinstance(cfg_theme, dict):
        return default_theme
    merged = dict(default_theme)
    for key in default_theme:
        value = cfg_theme.get(key)
        if isinstance(value, str) and value.strip():
            merged[key] = value.strip()
    return merged


def _build_email_html(business_name: str, staff_name: str, appt_date: date, rows: list, theme: dict) -> str:
    date_label = appt_date.strftime("%A, %B %d, %Y")
    total_count = len(rows)
    palette_name = theme.get("palette_name", "Signature")
    hero_gradient_start = theme.get("hero_gradient_start", "#0f172a")
    hero_gradient_end = theme.get("hero_gradient_end", "#1d4ed8")
    accent = theme.get("accent", "#2563eb")
    accent_soft = theme.get("accent_soft", "#dbeafe")
    table_header_bg = theme.get("table_header_bg", "#f1f5f9")
    table_header_text = theme.get("table_header_text", "#0f172a")
    panel_bg = theme.get("panel_bg", "#f8fafc")
    border = theme.get("border", "#e2e8f0")

    container_open = f"""
    <div style="font-family: 'Segoe UI', Arial, Helvetica, sans-serif; color: #1f2937; max-width: 760px; margin: 0 auto; padding: 6px;">
      <div style="background: linear-gradient(135deg, {hero_gradient_start}, {hero_gradient_end}); color: #ffffff; border-radius: 14px; padding: 20px 22px; box-shadow: 0 10px 22px rgba(15, 23, 42, 0.18);">
        <div style="font-size: 11px; letter-spacing: 0.12em; text-transform: uppercase; opacity: 0.9;">Daily Staff Schedule • {palette_name}</div>
        <h2 style="margin: 8px 0 2px 0; font-size: 24px; line-height: 1.2;">{business_name}</h2>
        <div style="font-size: 15px; opacity: 0.95;">Prepared for <strong>{staff_name}</strong> on {date_label}</div>
      </div>
      <div style="margin-top: 12px; background: {panel_bg}; border: 1px solid {border}; border-radius: 10px; padding: 10px 12px; font-size: 14px;">
        <span style="display:inline-block; background:{accent_soft}; color:#0f172a; border-radius:999px; padding:4px 10px; font-size:12px; margin-right:8px;">{staff_name}</span>
        <strong>Total appointments today:</strong> <span style="color:{accent};">{total_count}</span>
      </div>
    """
    if not rows:
        body = f"""
        <div style="margin-top: 12px; padding: 14px 16px; background:{panel_bg}; border:1px solid {border}; border-radius: 10px;">
          No appointments scheduled for today.
        </div>
        """
        footer = f"""
        <div style="margin-top: 16px; font-size: 12px; color: #64748b;">
          This is an automated schedule summary from Voice Facilitator.
        </div>
        """
        return container_open + body + footer + "</div>"

    table_rows = []
    for r in rows:
        table_rows.append(
            f"""
            <tr>
              <td style="padding:10px 12px; border-bottom:1px solid #e5e7eb;">{_format_time(r.get('appointment_time'))}</td>
              <td style="padding:10px 12px; border-bottom:1px solid #e5e7eb;">{r.get('service_name') or 'Service'}</td>
              <td style="padding:10px 12px; border-bottom:1px solid #e5e7eb;">{r.get('customer_name') or 'Customer'}</td>
              <td style="padding:10px 12px; border-bottom:1px solid #e5e7eb;">{r.get('customer_phone') or 'N/A'}</td>
              <td style="padding:10px 12px; border-bottom:1px solid #e5e7eb;">{r.get('duration_minutes') or 30} min</td>
            </tr>
            """
        )
    body = f"""
    <table style="margin-top: 12px; border-collapse: separate; border-spacing: 0; width:100%; font-size: 14px; border:1px solid {border}; border-radius: 10px; overflow: hidden;">
      <thead>
        <tr style="background:{table_header_bg}; color:{table_header_text};">
          <th align="left" style="padding:10px 12px; border-bottom:1px solid {border};">Time</th>
          <th align="left" style="padding:10px 12px; border-bottom:1px solid {border};">Service</th>
          <th align="left" style="padding:10px 12px; border-bottom:1px solid {border};">Customer</th>
          <th align="left" style="padding:10px 12px; border-bottom:1px solid {border};">Phone</th>
          <th align="left" style="padding:10px 12px; border-bottom:1px solid {border};">Duration</th>
        </tr>
      </thead>
      <tbody>
        {''.join(table_rows)}
      </tbody>
    </table>
    """
    footer = """
    <div style="margin-top: 16px; font-size: 12px; color: #64748b;">
      This is an automated schedule summary from Voice Facilitator.
    </div>
    """
    return container_open + body + footer + "</div>"


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
    target_date = _target_date(tz_name)
    business_name = config.get_business_name()
    business_id = _resolve_business_id(db, config)
    theme = _get_email_theme(config)
    staff_emails = _load_staff_emails(config)

    if not staff_emails:
        print("No staff emails found in config.")
        return

    appointments = _fetch_appointments_for_date(db, target_date, business_id)
    staff_groups = {}
    for row in appointments:
        staff_name = row.get("staff_name") or "Unassigned"
        staff_groups.setdefault(staff_name, []).append(row)

    for staff_name, email in staff_emails.items():
        rows = staff_groups.get(staff_name, [])
        subject = f"{business_name} • {staff_name} • schedule for {target_date.strftime('%b %d, %Y')}"
        html_body = _build_email_html(business_name, staff_name, target_date, rows, theme)
        if dry_run:
            print(f"[DRY_RUN] {business_name} | {staff_name} -> {email}: {len(rows)} appointments ({target_date.isoformat()})")
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
        print(f"Sent {business_name} | {staff_name} -> {email}: {len(rows)} appointments ({target_date.isoformat()})")


if __name__ == "__main__":
    main()
