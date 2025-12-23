import os
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

import pymysql
from dotenv import load_dotenv

# Load .env from project root (works when running scripts from repo root)
load_dotenv()

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root2")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "voice_facilitator")


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


def init_db():
    """
    Creates tables (idempotent).
    IMPORTANT: keep FK column types EXACTLY identical to referenced PK type.
    """
    ddl = """
    CREATE TABLE IF NOT EXISTS businesses (
      id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
      slug VARCHAR(64) NOT NULL UNIQUE,
      name VARCHAR(255) NOT NULL,
      timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Beirut',
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
      id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
      business_id BIGINT UNSIGNED NOT NULL,
      customer_id BIGINT UNSIGNED NOT NULL,
      staff_id BIGINT UNSIGNED NOT NULL,
      service_id BIGINT UNSIGNED NOT NULL,
      booking_type VARCHAR(32) NOT NULL DEFAULT 'phone',  -- phone, walkin, web
      status VARCHAR(32) NOT NULL DEFAULT 'confirmed',    -- confirmed, cancelled, no_show, completed
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
      id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
      business_id BIGINT UNSIGNED NOT NULL,
      customer_id BIGINT UNSIGNED NULL,
      session_id VARCHAR(64) NOT NULL,
      role VARCHAR(16) NOT NULL,          -- user/assistant/system
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

    CREATE TABLE IF NOT EXISTS calls (
      id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
      business_id BIGINT UNSIGNED NOT NULL,
      customer_id BIGINT UNSIGNED NULL,
      session_id VARCHAR(64) NOT NULL,
      started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
      ended_at TIMESTAMP NULL,
      INDEX idx_calls_session (session_id),
      INDEX idx_calls_business_time (business_id, started_at),
      CONSTRAINT fk_calls_business
        FOREIGN KEY (business_id) REFERENCES businesses(id)
        ON DELETE CASCADE
    ) ENGINE=InnoDB;
    """
    with db_cursor() as cur:
        for stmt in ddl.split(";"):
            s = stmt.strip()
            if s:
                cur.execute(s)


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
    # deterministic slug from name
    slug = (
        (name or "business")
        .strip()
        .lower()
        .replace("&", "and")
        .replace(" ", "_")
        .replace("-", "_")
    )
    return ensure_business(slug=slug, name=name, timezone=timezone)


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


def log_call_start(session_id: str, business_id: int, customer_id: Optional[int] = None) -> int:
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO calls (business_id, customer_id, session_id) VALUES (%s,%s,%s)",
            (business_id, customer_id, session_id),
        )
        return int(cur.lastrowid)


def log_call_end(call_id: int):
    with db_cursor() as cur:
        cur.execute("UPDATE calls SET ended_at=NOW() WHERE id=%s", (call_id,))


def log_message(
    call_id: int,
    role: str,
    text: str,
    intent: Optional[str] = None,
    confidence: Optional[float] = None,
    entities: Optional[Dict[str, Any]] = None,
):
    # Resolve business_id from call (so you never FK-mismatch)
    with db_cursor() as cur:
        cur.execute("SELECT business_id, customer_id, session_id FROM calls WHERE id=%s", (call_id,))
        call = cur.fetchone()
        if not call:
            raise RuntimeError(f"log_message: call_id={call_id} not found")

        cur.execute(
            """
            INSERT INTO conversation_events
            (business_id, customer_id, session_id, role, text, intent, confidence, entities_json)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                call["business_id"],
                call["customer_id"],
                call["session_id"],
                role,
                text,
                intent,
                confidence,
                (entities if entities is not None else None),
            ),
        )


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