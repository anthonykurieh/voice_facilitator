import json
import os
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pymysql
from dotenv import load_dotenv

# Load .env from project root automatically (works if you run from repo root)
load_dotenv()

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "")


def get_connection():
    if not DB_NAME:
        raise RuntimeError("DB_NAME is not set. Set it in .env (DB_NAME=voice_facilitator).")

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


# =========================
# Schema (idempotent)
# =========================

def init_db():
    ddl = """
    CREATE TABLE IF NOT EXISTS businesses (
      id BIGINT PRIMARY KEY AUTO_INCREMENT,
      slug VARCHAR(64) NOT NULL UNIQUE,
      name VARCHAR(255) NOT NULL,
      timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Dubai',
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
      currency VARCHAR(8) NOT NULL DEFAULT 'AED',
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
      id BIGINT PRIMARY KEY AUTO_INCREMENT,
      business_id BIGINT NOT NULL,
      customer_id BIGINT NOT NULL,
      staff_id BIGINT NOT NULL,
      service_id BIGINT NOT NULL,
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

    CREATE TABLE IF NOT EXISTS conversation_events (
      id BIGINT PRIMARY KEY AUTO_INCREMENT,
      business_id BIGINT NOT NULL,
      customer_id BIGINT NULL,
      session_id VARCHAR(64) NOT NULL,
      role VARCHAR(16) NOT NULL,
      text LONGTEXT NULL,
      intent VARCHAR(64) NULL,
      confidence DECIMAL(5,4) NULL,
      entities_json JSON NULL,
      created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      INDEX idx_conv_session (session_id),
      INDEX idx_conv_business_time (business_id, created_at),
      CONSTRAINT fk_conv_business
        FOREIGN KEY (business_id) REFERENCES businesses(id)
        ON DELETE CASCADE
    ) ENGINE=InnoDB;
    """

    with db_cursor() as cur:
        for stmt in ddl.split(";"):
            s = stmt.strip()
            if s:
                cur.execute(s)


# =========================
# Business / Customer
# =========================

def ensure_business(slug: str, name: str, timezone: str = "Asia/Dubai") -> int:
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


def get_or_create_business(name: str, slug: Optional[str] = None, timezone: str = "Asia/Dubai") -> int:
    """
    Used by simulate_voice_call. If slug isn't provided, derive one from the name.
    """
    slug_val = slug or name.strip().lower().replace(" ", "_")[:64]
    return ensure_business(slug=slug_val, name=name, timezone=timezone)


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
            raise RuntimeError("upsert_customer: failed to fetch after upsert.")
        return int(row["id"])


# =========================
# Conversation logging
# =========================

def log_message(
    call_id: str,
    role: str,
    text: Optional[str],
    *,
    business_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    session_id: Optional[str] = None,
    intent: Optional[str] = None,
    confidence: Optional[float] = None,
    entities: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Writes to conversation_events.
    NOTE: entities MUST be JSON-encoded string for PyMySQL.
    """
    entities_json = None
    if entities is not None:
        entities_json = json.dumps(entities, ensure_ascii=False)

    # If simulate_voice_call uses call_id but your schema logs by session_id,
    # we store call_id inside session_id if session_id not provided.
    sess = session_id or str(call_id)

    if business_id is None:
        # we can't infer business_id safely: caller should pass it
        business_id = 1  # minimal default; better: always pass business_id

    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO conversation_events
              (business_id, customer_id, session_id, role, text, intent, confidence, entities_json)
            VALUES
              (%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (business_id, customer_id, sess, role, text, intent, confidence, entities_json),
        )


# =========================
# Services
# =========================

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
            "SELECT id, code, name, duration_min, price, currency FROM services WHERE business_id=%s AND active=1",
            (business_id,),
        )
        return cur.fetchall()


# =========================
# Appointments CRUD
# =========================

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
              (business_id, customer_id, staff_id, service_id, booking_type, start_time, end_time, quoted_price, currency, notes)
            VALUES
              (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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


def get_appointment_detail(appointment_id: int) -> Optional[Dict[str, Any]]:
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT
              a.*,
              s.name AS service_name,
              s.code AS service_code,
              st.name AS staff_name
            FROM appointments a
            JOIN services s ON s.id=a.service_id
            JOIN staff st ON st.id=a.staff_id
            WHERE a.id=%s
            """,
            (appointment_id,),
        )
        return cur.fetchone()


def list_upcoming_appointments(business_id: int, customer_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT
              a.id,
              a.start_time,
              a.end_time,
              a.status,
              s.name AS service_name,
              st.name AS staff_name
            FROM appointments a
            JOIN services s ON s.id=a.service_id
            JOIN staff st ON st.id=a.staff_id
            WHERE a.business_id=%s
              AND a.customer_id=%s
              AND a.start_time >= NOW()
              AND a.status IN ('confirmed','completed')
            ORDER BY a.start_time ASC
            LIMIT %s
            """,
            (business_id, customer_id, limit),
        )
        return cur.fetchall()


def find_appointment_candidates(
    business_id: int,
    customer_id: int,
    *,
    status_in: Tuple[str, ...] = ("confirmed", "completed"),
    future_only: bool = True,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    where_time = "AND a.start_time >= NOW()" if future_only else ""
    placeholders = ",".join(["%s"] * len(status_in))

    with db_cursor() as cur:
        cur.execute(
            f"""
            SELECT
              a.id,
              a.start_time,
              a.end_time,
              a.status,
              s.name AS service_name,
              st.name AS staff_name
            FROM appointments a
            JOIN services s ON s.id=a.service_id
            JOIN staff st ON st.id=a.staff_id
            WHERE a.business_id=%s
              AND a.customer_id=%s
              AND a.status IN ({placeholders})
              {where_time}
            ORDER BY a.start_time ASC
            LIMIT %s
            """,
            (business_id, customer_id, *status_in, limit),
        )
        return cur.fetchall()


def cancel_appointment(appointment_id: int, note: Optional[str] = None) -> None:
    with db_cursor() as cur:
        cur.execute(
            """
            UPDATE appointments
            SET status='cancelled',
                notes=COALESCE(%s, notes)
            WHERE id=%s
            """,
            (note, appointment_id),
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
            SET staff_id=%s,
                start_time=%s,
                end_time=%s,
                notes=CASE
                    WHEN %s IS NULL THEN notes
                    WHEN notes IS NULL OR notes='' THEN %s
                    ELSE CONCAT(notes, '\n', %s)
                END
            WHERE id=%s
            """,
            (new_staff_id, new_start_time, new_end_time, note, note, note, appointment_id),
        )


def update_appointment_service(
    appointment_id: int,
    new_service_id: int,
    new_end_time: Optional[datetime] = None,
    note: Optional[str] = None,
) -> None:
    """
    Update service_id for an appointment.
    If you already computed a new end_time (service duration changed), pass it.
    """
    with db_cursor() as cur:
        if new_end_time is None:
            cur.execute(
                """
                UPDATE appointments
                SET service_id=%s,
                    notes=CASE
                        WHEN %s IS NULL THEN notes
                        WHEN notes IS NULL OR notes='' THEN %s
                        ELSE CONCAT(notes, '\n', %s)
                    END
                WHERE id=%s
                """,
                (new_service_id, note, note, note, appointment_id),
            )
        else:
            cur.execute(
                """
                UPDATE appointments
                SET service_id=%s,
                    end_time=%s,
                    notes=CASE
                        WHEN %s IS NULL THEN notes
                        WHEN notes IS NULL OR notes='' THEN %s
                        ELSE CONCAT(notes, '\n', %s)
                    END
                WHERE id=%s
                """,
                (new_service_id, new_end_time, note, note, note, appointment_id),
            )

from typing import Optional, List, Dict, Any
from datetime import datetime


# -------------------------
# Call “lifecycle” helpers
# -------------------------

def log_call_start(session_id: str, business_id: int) -> str:
    """
    You don't have a calls table right now.
    We'll treat call_id == session_id to keep simulate_voice_call working.
    """
    return str(session_id)


def log_call_end(call_id: str) -> None:
    """
    No-op until you add a calls table.
    """
    return


def attach_customer_to_call(call_id: str, customer_id: int) -> None:
    """
    No calls table. No-op for now.
    We still keep the function so imports don't break.
    """
    return


# -------------------------
# Appointment reads
# -------------------------

def get_appointment_detail(business_id: int, customer_id: int, appointment_id: int) -> Optional[Dict[str, Any]]:
    """
    Returns appointment + service/staff fields used by simulate_voice_call.py:
    - service_name
    - duration_min
    - staff_name
    - start_time/end_time/status
    """
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT
              a.id,
              a.business_id,
              a.customer_id,
              a.staff_id,
              a.service_id,
              a.status,
              a.start_time,
              a.end_time,
              a.notes,
              s.name AS service_name,
              s.code AS service_code,
              s.duration_min AS duration_min,
              s.price AS service_price,
              s.currency AS service_currency,
              st.name AS staff_name
            FROM appointments a
            JOIN services s ON s.id=a.service_id
            JOIN staff st ON st.id=a.staff_id
            WHERE a.business_id=%s
              AND a.customer_id=%s
              AND a.id=%s
            LIMIT 1
            """,
            (business_id, customer_id, appointment_id),
        )
        return cur.fetchone()


def list_upcoming_appointments(
    business_id: int,
    customer_id: int,
    *,
    now: Optional[datetime] = None,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """
    simulate_voice_call passes now=datetime.now().
    """
    now = now or datetime.now()
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT
              a.id,
              a.start_time,
              a.end_time,
              a.status,
              s.name AS service_name,
              st.name AS staff_name
            FROM appointments a
            JOIN services s ON s.id=a.service_id
            JOIN staff st ON st.id=a.staff_id
            WHERE a.business_id=%s
              AND a.customer_id=%s
              AND a.start_time >= %s
              AND a.status IN ('confirmed','completed')
            ORDER BY a.start_time ASC
            LIMIT %s
            """,
            (business_id, customer_id, now, limit),
        )
        return cur.fetchall()


# -------------------------
# Appointment mutations
# -------------------------

def cancel_appointment(business_id: int, customer_id: int, appointment_id: int, *, reason: Optional[str] = None) -> bool:
    """
    Only cancels if it's the customer's appointment and currently confirmed.
    Returns True if updated, False otherwise.
    """
    note = f"cancel_reason={reason}" if reason else None
    with db_cursor() as cur:
        cur.execute(
            """
            UPDATE appointments
            SET status='cancelled',
                notes=CASE
                  WHEN %s IS NULL THEN notes
                  WHEN notes IS NULL OR notes='' THEN %s
                  ELSE CONCAT(notes, '\n', %s)
                END
            WHERE business_id=%s
              AND customer_id=%s
              AND id=%s
              AND status='confirmed'
            """,
            (note, note, note, business_id, customer_id, appointment_id),
        )
        return cur.rowcount > 0


def update_appointment_time_and_staff(
    business_id: int,
    customer_id: int,
    appointment_id: int,
    *,
    new_staff_id: int,
    new_start: datetime,
    new_end: datetime,
    note: Optional[str] = None,
) -> bool:
    """
    Reschedules (and/or changes barber) for a confirmed appointment.
    Returns True if updated.
    """
    with db_cursor() as cur:
        cur.execute(
            """
            UPDATE appointments
            SET staff_id=%s,
                start_time=%s,
                end_time=%s,
                notes=CASE
                  WHEN %s IS NULL THEN notes
                  WHEN notes IS NULL OR notes='' THEN %s
                  ELSE CONCAT(notes, '\n', %s)
                END
            WHERE business_id=%s
              AND customer_id=%s
              AND id=%s
              AND status='confirmed'
            """,
            (new_staff_id, new_start, new_end, note, note, note, business_id, customer_id, appointment_id),
        )
        return cur.rowcount > 0


def update_appointment_service(
    business_id: int,
    customer_id: int,
    appointment_id: int,
    *,
    new_service_id: int,
    new_end: datetime,
    new_price: float,
    currency: str,
    note: Optional[str] = None,
) -> bool:
    """
    Changes service, and updates end_time + quoted_price/currency.
    Returns True if updated.
    """
    with db_cursor() as cur:
        cur.execute(
            """
            UPDATE appointments
            SET service_id=%s,
                end_time=%s,
                quoted_price=%s,
                currency=%s,
                notes=CASE
                  WHEN %s IS NULL THEN notes
                  WHEN notes IS NULL OR notes='' THEN %s
                  ELSE CONCAT(notes, '\n', %s)
                END
            WHERE business_id=%s
              AND customer_id=%s
              AND id=%s
              AND status='confirmed'
            """,
            (
                new_service_id, new_end, new_price, currency,
                note, note, note,
                business_id, customer_id, appointment_id
            ),
        )
        return cur.rowcount > 0

def update_appointment_time_and_staff(
    appointment_id: int,
    new_staff_id: int,
    new_start_time: datetime,
    new_end_time: datetime,
    note: Optional[str] = None,
) -> None:
    """
    Backwards-compatible name used by simulate_voice_call.py.
    """
    reschedule_appointment(
        appointment_id=appointment_id,
        new_staff_id=new_staff_id,
        new_start_time=new_start_time,
        new_end_time=new_end_time,
        note=note,
    )

from typing import Optional

def get_staff_name(business_id: int, staff_id: int) -> Optional[str]:
    """
    Returns staff.name for the given business + staff id.
    Used by availability.py.
    """
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT name
            FROM staff
            WHERE business_id=%s AND id=%s
            LIMIT 1
            """,
            (business_id, staff_id),
        )
        row = cur.fetchone()
        return str(row["name"]) if row else None