import os
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pymysql
from dotenv import load_dotenv


# ----------------------------
# Robust .env loading
# ----------------------------
def _find_project_root(start: Path) -> Path:
    cur = start
    for _ in range(10):
        if (cur / ".env").exists():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return start


_THIS_FILE = Path(__file__).resolve()
_PROJECT_ROOT = _find_project_root(_THIS_FILE.parent)
_ENV_PATH = _PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=_ENV_PATH, override=False)


DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root2")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "voice_facilitator")


def get_connection():
    if not DB_NAME:
        raise RuntimeError("DB_NAME is not set. Set DB_NAME=voice_facilitator in .env")

    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4",
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
    )


@contextmanager
def db_cursor():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ----------------------------
# Schema (idempotent)
# ----------------------------
def init_db():
    ddl = """
    CREATE TABLE IF NOT EXISTS businesses (
      id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
      slug VARCHAR(64) NOT NULL UNIQUE,
      name VARCHAR(255) NOT NULL,
      timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Dubai',
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB;

    CREATE TABLE IF NOT EXISTS customers (
      id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
      business_id BIGINT UNSIGNED NOT NULL,
      name VARCHAR(255) NOT NULL,
      phone VARCHAR(64) NOT NULL,
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE KEY uniq_customer_phone (business_id, phone),
      CONSTRAINT fk_customers_business
        FOREIGN KEY (business_id) REFERENCES businesses(id)
        ON DELETE CASCADE
    ) ENGINE=InnoDB;

    CREATE TABLE IF NOT EXISTS staff (
      id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
      business_id BIGINT UNSIGNED NOT NULL,
      name VARCHAR(255) NOT NULL,
      specialty VARCHAR(255) NULL,
      active TINYINT NOT NULL DEFAULT 1,
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      INDEX idx_staff_business (business_id),
      CONSTRAINT fk_staff_business
        FOREIGN KEY (business_id) REFERENCES businesses(id)
        ON DELETE CASCADE
    ) ENGINE=InnoDB;

    CREATE TABLE IF NOT EXISTS services (
      id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
      business_id BIGINT UNSIGNED NOT NULL,
      code VARCHAR(64) NOT NULL,
      name VARCHAR(255) NOT NULL,
      duration_min INT NOT NULL,
      price DECIMAL(10,2) NOT NULL,
      currency VARCHAR(8) NOT NULL DEFAULT 'AED',
      active TINYINT NOT NULL DEFAULT 1,
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE KEY uniq_service_code (business_id, code),
      CONSTRAINT fk_services_business
        FOREIGN KEY (business_id) REFERENCES businesses(id)
        ON DELETE CASCADE
    ) ENGINE=InnoDB;

    CREATE TABLE IF NOT EXISTS staff_services (
      business_id BIGINT UNSIGNED NOT NULL,
      staff_id BIGINT UNSIGNED NOT NULL,
      service_id BIGINT UNSIGNED NOT NULL,
      PRIMARY KEY (business_id, staff_id, service_id),
      CONSTRAINT fk_staff_services_business
        FOREIGN KEY (business_id) REFERENCES businesses(id)
        ON DELETE CASCADE,
      CONSTRAINT fk_staff_services_staff
        FOREIGN KEY (staff_id) REFERENCES staff(id)
        ON DELETE CASCADE,
      CONSTRAINT fk_staff_services_service
        FOREIGN KEY (service_id) REFERENCES services(id)
        ON DELETE CASCADE
    ) ENGINE=InnoDB;

    CREATE TABLE IF NOT EXISTS business_hours (
      id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
      business_id BIGINT UNSIGNED NOT NULL,
      dow TINYINT NOT NULL,
      open_time TIME NOT NULL,
      close_time TIME NOT NULL,
      is_closed TINYINT NOT NULL DEFAULT 0,
      UNIQUE KEY uniq_hours (business_id, dow),
      CONSTRAINT fk_hours_business
        FOREIGN KEY (business_id) REFERENCES businesses(id)
        ON DELETE CASCADE
    ) ENGINE=InnoDB;

    CREATE TABLE IF NOT EXISTS appointments (
      id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
      business_id BIGINT UNSIGNED NOT NULL,
      customer_id BIGINT UNSIGNED NOT NULL,
      staff_id BIGINT UNSIGNED NOT NULL,
      service_id BIGINT UNSIGNED NOT NULL,
      booking_type VARCHAR(32) NOT NULL DEFAULT 'phone',
      status VARCHAR(32) NOT NULL DEFAULT 'confirmed',
      start_time DATETIME NOT NULL,
      end_time DATETIME NOT NULL,
      quoted_price DECIMAL(10,2) NOT NULL,
      currency VARCHAR(8) NOT NULL,
      notes TEXT NULL,
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      INDEX idx_appt_business_time (business_id, start_time),
      INDEX idx_appt_staff_time (staff_id, start_time),
      CONSTRAINT fk_appt_business
        FOREIGN KEY (business_id) REFERENCES businesses(id)
        ON DELETE CASCADE,
      CONSTRAINT fk_appt_customer
        FOREIGN KEY (customer_id) REFERENCES customers(id)
        ON DELETE CASCADE,
      CONSTRAINT fk_appt_staff
        FOREIGN KEY (staff_id) REFERENCES staff(id)
        ON DELETE RESTRICT,
      CONSTRAINT fk_appt_service
        FOREIGN KEY (service_id) REFERENCES services(id)
        ON DELETE RESTRICT
    ) ENGINE=InnoDB;

    CREATE TABLE IF NOT EXISTS calls (
      id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
      business_id BIGINT UNSIGNED NOT NULL,
      session_id VARCHAR(64) NOT NULL,
      started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      ended_at TIMESTAMP NULL,
      INDEX idx_calls_business_time (business_id, started_at),
      INDEX idx_calls_session (session_id),
      CONSTRAINT fk_calls_business
        FOREIGN KEY (business_id) REFERENCES businesses(id)
        ON DELETE CASCADE
    ) ENGINE=InnoDB;

    CREATE TABLE IF NOT EXISTS conversation_events (
      id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
      business_id BIGINT UNSIGNED NOT NULL,
      call_id BIGINT UNSIGNED NULL,
      customer_id BIGINT UNSIGNED NULL,
      session_id VARCHAR(64) NOT NULL,
      role VARCHAR(16) NOT NULL,
      text LONGTEXT NULL,
      intent VARCHAR(64) NULL,
      confidence DECIMAL(5,4) NULL,
      entities_json JSON NULL,
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      INDEX idx_conv_session (session_id),
      INDEX idx_conv_business_time (business_id, created_at),
      INDEX idx_conv_call (call_id),
      CONSTRAINT fk_conv_business
        FOREIGN KEY (business_id) REFERENCES businesses(id)
        ON DELETE CASCADE,
      CONSTRAINT fk_conv_call
        FOREIGN KEY (call_id) REFERENCES calls(id)
        ON DELETE SET NULL
    ) ENGINE=InnoDB;
    """
    with db_cursor() as cur:
        for stmt in ddl.split(";"):
            s = stmt.strip()
            if s:
                cur.execute(s)


# ----------------------------
# Business helpers
# ----------------------------
def ensure_business(slug: str, name: str, timezone: str = "Asia/Dubai") -> int:
    """
    Original name expected by seed_data.py.
    """
    with db_cursor() as cur:
        cur.execute("SELECT id FROM businesses WHERE slug=%s", (slug,))
        row = cur.fetchone()
        if row:
            return int(row["id"])
        cur.execute(
            "INSERT INTO businesses (slug, name, timezone) VALUES (%s,%s,%s)",
            (slug, name, timezone),
        )
        return int(cur.lastrowid)


def get_or_create_business(name: str, slug: str = "barber_demo", timezone: str = "Asia/Dubai") -> int:
    """
    New name used by simulate_voice_call.py.
    """
    return ensure_business(slug=slug, name=name, timezone=timezone)


# ----------------------------
# Customer helpers
# ----------------------------
def upsert_customer(business_id: int, name: str, phone: str) -> int:
    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO customers (business_id, name, phone)
            VALUES (%s,%s,%s)
            ON DUPLICATE KEY UPDATE name=VALUES(name)
            """,
            (business_id, name, phone),
        )
        if cur.lastrowid:
            return int(cur.lastrowid)

        cur.execute(
            "SELECT id FROM customers WHERE business_id=%s AND phone=%s",
            (business_id, phone),
        )
        row = cur.fetchone()
        if not row:
            raise RuntimeError("upsert_customer: failed to fetch customer after upsert.")
        return int(row["id"])


# ----------------------------
# Service helpers (needed by availability)
# ----------------------------
def get_service_by_name_or_code(business_id: int, service_text: str) -> Optional[Dict[str, Any]]:
    s = (service_text or "").strip().lower()
    if not s:
        return None

    with db_cursor() as cur:
        cur.execute(
            """
            SELECT *
            FROM services
            WHERE business_id=%s AND active=1
              AND (LOWER(code)=%s OR LOWER(name)=%s OR LOWER(name) LIKE %s)
            LIMIT 1
            """,
            (business_id, s, s, f"%{s}%"),
        )
        return cur.fetchone()


def list_services(business_id: int) -> List[Dict[str, Any]]:
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT id, code, name, duration_min, price, currency
            FROM services
            WHERE business_id=%s AND active=1
            ORDER BY id
            """,
            (business_id,),
        )
        return cur.fetchall()


# ----------------------------
# Calls / logs (minimal baseline)
# ----------------------------
def log_call_start(session_id: str, business_id: int) -> int:
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO calls (business_id, session_id) VALUES (%s,%s)",
            (business_id, session_id),
        )
        return int(cur.lastrowid)


def log_call_end(call_id: int) -> None:
    with db_cursor() as cur:
        cur.execute("UPDATE calls SET ended_at=CURRENT_TIMESTAMP WHERE id=%s", (call_id,))


def log_message(call_id: int, role: str, text: str) -> None:
    with db_cursor() as cur:
        cur.execute("SELECT business_id, session_id FROM calls WHERE id=%s", (call_id,))
        row = cur.fetchone()
        if not row:
            raise RuntimeError(f"log_message: call_id {call_id} not found")
        business_id = int(row["business_id"])
        session_id = str(row["session_id"])

        cur.execute(
            """
            INSERT INTO conversation_events
              (business_id, call_id, session_id, role, text)
            VALUES
              (%s,%s,%s,%s,%s)
            """,
            (business_id, call_id, session_id, role, text),
        )


# ----------------------------
# Appointment creation used by simulator (service_name -> service_id)
# ----------------------------
def create_appointment(
    *,
    business_id: int,
    customer_id: int,
    service_name: str,
    booking_type: str,
    appointment_date: str,   # YYYY-MM-DD
    appointment_time: str,   # HH:MM
    staff_id: int,
    notes: Optional[str] = None,
) -> int:
    svc = get_service_by_name_or_code(business_id, service_name)
    if not svc:
        raise RuntimeError(f"Unknown service '{service_name}' for business_id={business_id}")

    service_id = int(svc["id"])
    duration_min = int(svc["duration_min"])
    quoted_price = float(svc["price"])
    currency = str(svc["currency"])

    start_dt = datetime.fromisoformat(f"{appointment_date}T{appointment_time}:00")
    end_dt = start_dt + timedelta(minutes=duration_min)

    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO appointments
              (business_id, customer_id, staff_id, service_id, booking_type, status,
               start_time, end_time, quoted_price, currency, notes)
            VALUES
              (%s,%s,%s,%s,%s,'confirmed',%s,%s,%s,%s,%s)
            """,
            (
                business_id,
                customer_id,
                staff_id,
                service_id,
                booking_type,
                start_dt,
                end_dt,
                quoted_price,
                currency,
                notes,
            ),
        )
        return int(cur.lastrowid)