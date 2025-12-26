import json
import os
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pymysql
from dotenv import load_dotenv

# Load .env from project root
load_dotenv()

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "")


def get_connection():
    if not DB_NAME:
        raise RuntimeError("DB_NAME is not set. Set DB_NAME in your .env (e.g., DB_NAME=voice_facilitator).")

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


def _json_or_none(obj: Optional[Dict[str, Any]]) -> Optional[str]:
    if obj is None:
        return None
    # pymysql cannot pass dict directly; must be JSON string
    return json.dumps(obj, ensure_ascii=False)


def init_db():
    """
    Creates tables (idempotent).
    IMPORTANT: This assumes DB_NAME already exists in MySQL.
    """
    ddl = """
    CREATE TABLE IF NOT EXISTS businesses (
      id BIGINT PRIMARY KEY AUTO_INCREMENT,
      slug VARCHAR(64) NOT NULL UNIQUE,
      name VARCHAR(255) NOT NULL,
      timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Beirut',
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB;

    CREATE TABLE IF NOT EXISTS customers (
      id BIGINT PRIMARY KEY AUTO_INCREMENT,
      business_id BIGINT NOT NULL,
      name VARCHAR(255) NOT NULL,
      phone VARCHAR(64) NOT NULL,
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE KEY uniq_customer_phone (business_id, phone),
      CONSTRAINT fk_customers_business
        FOREIGN KEY (business_id) REFERENCES businesses(id)
        ON DELETE CASCADE
    ) ENGINE=InnoDB;

    CREATE TABLE IF NOT EXISTS staff (
      id BIGINT PRIMARY KEY AUTO_INCREMENT,
      business_id BIGINT NOT NULL,
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
      id BIGINT PRIMARY KEY AUTO_INCREMENT,
      business_id BIGINT NOT NULL,
      code VARCHAR(64) NOT NULL,
      name VARCHAR(255) NOT NULL,
      duration_min INT NOT NULL,
      price DECIMAL(10,2) NOT NULL,
      currency VARCHAR(8) NOT NULL DEFAULT 'LBP',
      active TINYINT NOT NULL DEFAULT 1,
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE KEY uniq_service_code (business_id, code),
      CONSTRAINT fk_services_business
        FOREIGN KEY (business_id) REFERENCES businesses(id)
        ON DELETE CASCADE
    ) ENGINE=InnoDB;

    CREATE TABLE IF NOT EXISTS staff_services (
      business_id BIGINT NOT NULL,
      staff_id BIGINT NOT NULL,
      service_id BIGINT NOT NULL,
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
      id BIGINT PRIMARY KEY AUTO_INCREMENT,
      business_id BIGINT NOT NULL,
      dow TINYINT NOT NULL,                 -- 0=Mon ... 6=Sun
      open_time TIME NOT NULL,
      close_time TIME NOT NULL,
      is_closed TINYINT NOT NULL DEFAULT 0,
      UNIQUE KEY uniq_hours (business_id, dow),
      CONSTRAINT fk_hours_business
        FOREIGN KEY (business_id) REFERENCES businesses(id)
        ON DELETE CASCADE
    ) ENGINE=InnoDB;

    CREATE TABLE IF NOT EXISTS appointments (
      id BIGINT PRIMARY KEY AUTO_INCREMENT,
      business_id BIGINT NOT NULL,
      customer_id BIGINT NOT NULL,
      staff_id BIGINT NOT NULL,
      service_id BIGINT NOT NULL,
      booking_type VARCHAR(32) NOT NULL DEFAULT 'phone',  -- phone, walkin, web
      status VARCHAR(32) NOT NULL DEFAULT 'confirmed',    -- confirmed, cancelled, no_show, completed
      start_time DATETIME NOT NULL,
      end_time DATETIME NOT NULL,
      quoted_price DECIMAL(10,2) NOT NULL,
      currency VARCHAR(8) NOT NULL,
      notes TEXT NULL,
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

      INDEX idx_appt_business_time (business_id, start_time),
      INDEX idx_appt_customer_time (customer_id, start_time),
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

    -- A "call session" row (so we can link messages + customer to a call)
    CREATE TABLE IF NOT EXISTS calls (
      id BIGINT PRIMARY KEY AUTO_INCREMENT,
      session_id VARCHAR(64) NOT NULL,
      business_id BIGINT NOT NULL,
      customer_id BIGINT NULL,
      started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      ended_at TIMESTAMP NULL,
      INDEX idx_calls_session (session_id),
      INDEX idx_calls_business_time (business_id, started_at),
      CONSTRAINT fk_calls_business
        FOREIGN KEY (business_id) REFERENCES businesses(id)
        ON DELETE CASCADE
    ) ENGINE=InnoDB;

    -- Message log for conversational analytics
    CREATE TABLE IF NOT EXISTS messages (
      id BIGINT PRIMARY KEY AUTO_INCREMENT,
      call_id BIGINT NOT NULL,
      role VARCHAR(16) NOT NULL,          -- user/assistant/system
      text LONGTEXT NULL,
      intent VARCHAR(64) NULL,
      confidence DECIMAL(5,4) NULL,
      entities_json JSON NULL,
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      INDEX idx_messages_call (call_id),
      INDEX idx_messages_time (created_at),
      CONSTRAINT fk_messages_call
        FOREIGN KEY (call_id) REFERENCES calls(id)
        ON DELETE CASCADE
    ) ENGINE=InnoDB;
    """

    with db_cursor() as cur:
        # Split on ";" safely for this simple DDL bundle
        for stmt in ddl.split(";"):
            s = stmt.strip()
            if s:
                cur.execute(s)


# ---------------------------
# Business + Customer
# ---------------------------

def ensure_business(slug: str, name: str, timezone: str = "Asia/Beirut") -> int:
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


def get_or_create_business(name: str, timezone: str = "Asia/Beirut") -> int:
    """
    Convenience: slugify-like behavior for demo usage.
    """
    slug = name.strip().lower().replace(" ", "_")[:64]
    return ensure_business(slug=slug, name=name, timezone=timezone)


def upsert_customer(business_id: int, name: str, phone: str) -> int:
    """
    Insert-or-update customer by (business_id, phone).
    """
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


# ---------------------------
# Calls + Messages logging
# ---------------------------

def log_call_start(session_id: str, business_id: int) -> int:
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO calls (session_id, business_id) VALUES (%s,%s)",
            (session_id, business_id),
        )
        return int(cur.lastrowid)


def attach_customer_to_call(call_id: int, customer_id: int) -> None:
    with db_cursor() as cur:
        cur.execute(
            "UPDATE calls SET customer_id=%s WHERE id=%s",
            (customer_id, call_id),
        )


def log_call_end(call_id: int) -> None:
    with db_cursor() as cur:
        cur.execute(
            "UPDATE calls SET ended_at=CURRENT_TIMESTAMP WHERE id=%s",
            (call_id,),
        )


def log_message(
    call_id: int,
    role: str,
    text: Optional[str],
    *,
    intent: Optional[str] = None,
    confidence: Optional[float] = None,
    entities: Optional[Dict[str, Any]] = None,
) -> None:
    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO messages
              (call_id, role, text, intent, confidence, entities_json)
            VALUES
              (%s,%s,%s,%s,%s,%s)
            """,
            (call_id, role, text, intent, confidence, _json_or_none(entities)),
        )


# ---------------------------
# Services
# ---------------------------

def get_service_by_name_or_code(business_id: int, service_text: str) -> Optional[Dict[str, Any]]:
    s = (service_text or "").strip().lower()
    if not s:
        return None

    with db_cursor() as cur:
        cur.execute(
            """
            SELECT id, code, name, duration_min, price, currency
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
            ORDER BY name
            """,
            (business_id,),
        )
        return cur.fetchall()


# ---------------------------
# Appointments CRUD
# ---------------------------

def create_appointment(
    business_id: int,
    customer_id: int,
    staff_id: int,
    service_id: int,
    booking_type: str,
    start_time: datetime,
    end_time: datetime,
    quoted_price: float,
    currency: str,
    notes: Optional[str] = None,
) -> int:
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
                start_time,
                end_time,
                quoted_price,
                currency,
                notes,
            ),
        )
        return int(cur.lastrowid)


def cancel_appointment(appointment_id: int, reason: Optional[str] = None) -> None:
    with db_cursor() as cur:
        cur.execute(
            "UPDATE appointments SET status='cancelled', notes=COALESCE(%s, notes) WHERE id=%s",
            (reason, appointment_id),
        )


def reschedule_appointment(
    appointment_id: int,
    new_staff_id: int,
    new_start_time: datetime,
    new_end_time: datetime,
    note: Optional[str] = None,
) -> None:
    with db_cursor() as cur:
        cur.execute(
            """
            UPDATE appointments
            SET staff_id=%s, start_time=%s, end_time=%s, notes=COALESCE(%s, notes)
            WHERE id=%s
            """,
            (new_staff_id, new_start_time, new_end_time, note, appointment_id),
        )


def get_appointment_detail(appointment_id: int) -> Optional[Dict[str, Any]]:
    """
    Used by the "modify/cancel" flows to read full appointment + human-friendly labels.
    """
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT
              a.id,
              a.business_id,
              a.customer_id,
              c.name AS customer_name,
              c.phone AS customer_phone,
              a.staff_id,
              st.name AS staff_name,
              a.service_id,
              sv.name AS service_name,
              sv.code AS service_code,
              a.booking_type,
              a.status,
              a.start_time,
              a.end_time,
              a.quoted_price,
              a.currency,
              a.notes,
              a.created_at
            FROM appointments a
            JOIN customers c ON c.id=a.customer_id
            JOIN staff st ON st.id=a.staff_id
            JOIN services sv ON sv.id=a.service_id
            WHERE a.id=%s
            """,
            (appointment_id,),
        )
        return cur.fetchone()


def list_upcoming_appointments(
    business_id: int,
    customer_id: Optional[int] = None,
    *,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """
    For "what are my upcoming appointments?" and to help disambiguate modifications.
    """
    with db_cursor() as cur:
        if customer_id is None:
            cur.execute(
                """
                SELECT
                  a.id,
                  a.start_time,
                  a.end_time,
                  a.status,
                  st.name AS staff_name,
                  sv.name AS service_name,
                  a.quoted_price,
                  a.currency
                FROM appointments a
                JOIN staff st ON st.id=a.staff_id
                JOIN services sv ON sv.id=a.service_id
                WHERE a.business_id=%s
                  AND a.start_time >= NOW()
                  AND a.status IN ('confirmed')
                ORDER BY a.start_time ASC
                LIMIT %s
                """,
                (business_id, limit),
            )
        else:
            cur.execute(
                """
                SELECT
                  a.id,
                  a.start_time,
                  a.end_time,
                  a.status,
                  st.name AS staff_name,
                  sv.name AS service_name,
                  a.quoted_price,
                  a.currency
                FROM appointments a
                JOIN staff st ON st.id=a.staff_id
                JOIN services sv ON sv.id=a.service_id
                WHERE a.business_id=%s
                  AND a.customer_id=%s
                  AND a.start_time >= NOW()
                  AND a.status IN ('confirmed')
                ORDER BY a.start_time ASC
                LIMIT %s
                """,
                (business_id, customer_id, limit),
            )
        return cur.fetchall()

def update_appointment_time_and_staff(
    appointment_id: int,
    new_staff_id: int,
    new_start_time: datetime,
    new_end_time: datetime,
    note: Optional[str] = None,
) -> None:
    """
    Backwards-compatible name used by simulate_voice_call.py.
    This simply calls the reschedule/update logic.
    """
    reschedule_appointment(
        appointment_id=appointment_id,
        new_staff_id=new_staff_id,
        new_start_time=new_start_time,
        new_end_time=new_end_time,
        note=note,
    )
def find_appointment_candidates(
    business_id: int,
    customer_id: Optional[int],
    *,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    status: str = "confirmed",
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """
    Candidate search for modify/cancel flows when user says:
    - "cancel my appointment"
    - "reschedule my haircut"
    and we need to find which one.
    """
    where = ["a.business_id=%s", "a.status=%s"]
    params: List[Any] = [business_id, status]

    if customer_id is not None:
        where.append("a.customer_id=%s")
        params.append(customer_id)

    if date_from is not None:
        where.append("a.start_time >= %s")
        params.append(date_from)

    if date_to is not None:
        where.append("a.start_time < %s")
        params.append(date_to)

    sql = f"""
    SELECT
      a.id,
      a.start_time,
      a.end_time,
      a.status,
      st.name AS staff_name,
      sv.name AS service_name,
      a.quoted_price,
      a.currency
    FROM appointments a
    JOIN staff st ON st.id=a.staff_id
    JOIN services sv ON sv.id=a.service_id
    WHERE {" AND ".join(where)}
    ORDER BY a.start_time ASC
    LIMIT %s
    """
    params.append(limit)

    with db_cursor() as cur:
        cur.execute(sql, tuple(params))
        return cur.fetchall()