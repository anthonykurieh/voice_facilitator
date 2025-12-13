import os
from contextlib import contextmanager
from datetime import datetime
from typing import Optional, Dict, Any, List

import pymysql
from pymysql.cursors import DictCursor
from dotenv import load_dotenv

from app.business.profile import BusinessProfile

load_dotenv()

MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "voice_user")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DB = os.getenv("MYSQL_DB", "voice_facilitator")


# ---------- CONNECTION & CONTEXT MANAGER ----------

def get_connection() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DB,
        cursorclass=DictCursor,
        autocommit=False,
        charset="utf8mb4",
    )


@contextmanager
def db_cursor():
    conn = get_connection()
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------- SCHEMA INITIALIZATION ----------

def init_db() -> None:
    """
    Create all tables if they don't exist (MySQL).
    Schema is designed for analytics + appointments + monetary value.
    """
    ddl_create = [
        # Businesses
        """
        CREATE TABLE IF NOT EXISTS businesses (
            id              INT AUTO_INCREMENT PRIMARY KEY,
            external_id     VARCHAR(255) NOT NULL UNIQUE,
            name            VARCHAR(255) NOT NULL,
            type            VARCHAR(255),
            timezone        VARCHAR(255),
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """,

        # Customers
        """
        CREATE TABLE IF NOT EXISTS customers (
            id              INT AUTO_INCREMENT PRIMARY KEY,
            business_id     INT NOT NULL,
            name            VARCHAR(255),
            phone           VARCHAR(64) NOT NULL,
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uniq_business_phone (business_id, phone),
            CONSTRAINT fk_customers_business
                FOREIGN KEY (business_id) REFERENCES businesses(id)
                ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """,

        # Services catalog (per business)
        """
        CREATE TABLE IF NOT EXISTS services (
            id                   INT AUTO_INCREMENT PRIMARY KEY,
            business_id          INT NOT NULL,
            code                 VARCHAR(64) NOT NULL,       -- e.g. HAIRCUT_MEN
            name                 VARCHAR(255) NOT NULL,      -- e.g. Men's Haircut
            default_duration_min INT NULL,
            base_price           DECIMAL(10,2) NULL,
            currency             VARCHAR(16) NULL,
            is_active            TINYINT(1) NOT NULL DEFAULT 1,
            created_at           DATETIME DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT fk_services_business
                FOREIGN KEY (business_id) REFERENCES businesses(id)
                ON DELETE CASCADE,
            UNIQUE KEY uniq_service_business (business_id, code)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """,

        # Calls (per conversation / session)
        """
        CREATE TABLE IF NOT EXISTS calls (
            id                      INT AUTO_INCREMENT PRIMARY KEY,
            session_id              VARCHAR(255) NOT NULL UNIQUE,
            business_id             INT NOT NULL,
            customer_id             INT NULL,
            started_at              DATETIME NOT NULL,
            ended_at                DATETIME NULL,
            raw_meta                TEXT NULL,

            -- Channel + analytics
            channel                 VARCHAR(64) NOT NULL DEFAULT 'phone',  -- phone, whatsapp, webchat...
            primary_intent          VARCHAR(255) NULL,
            primary_service         VARCHAR(255) NULL,
            outcome                 VARCHAR(64)  NULL,          -- BOOKED, INFO_ONLY, ABANDONED, CANCELLED...
            total_duration_sec      INT NULL,                   -- total call duration
            num_turns               INT NULL,                   -- number of back-and-forth turns
            total_estimated_value   DECIMAL(10,2) NULL,         -- sum of service values discussed/confirmed

            CONSTRAINT fk_calls_business
                FOREIGN KEY (business_id) REFERENCES businesses(id)
                ON DELETE CASCADE,
            CONSTRAINT fk_calls_customer
                FOREIGN KEY (customer_id) REFERENCES customers(id)
                ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """,

        # Call messages (per utterance)
        """
        CREATE TABLE IF NOT EXISTS call_messages (
            id              INT AUTO_INCREMENT PRIMARY KEY,
            call_id         INT NOT NULL,
            turn_index      INT NOT NULL,
            role            VARCHAR(32) NOT NULL,      -- 'user' or 'assistant'
            text            TEXT NOT NULL,
            intent          VARCHAR(255) NULL,        -- intent for user turns

            -- Per-turn extracted info
            service_name    VARCHAR(255) NULL,        -- identified service mentioned this turn
            amount          DECIMAL(10,2) NULL,       -- monetary amount mentioned
            currency        VARCHAR(16) NULL,         -- currency of that amount
            sentiment       VARCHAR(16) NULL,         -- neutral / frustrated / angry / positive
            entities_json   TEXT NULL,                -- raw entities blob from NLU

            timestamp       DATETIME NOT NULL,

            CONSTRAINT fk_call_messages_call
                FOREIGN KEY (call_id) REFERENCES calls(id)
                ON DELETE CASCADE,
            INDEX idx_call_messages_call_id (call_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """,

        # Appointments (actual bookings)
        """
        CREATE TABLE IF NOT EXISTS appointments (
            id                  INT AUTO_INCREMENT PRIMARY KEY,
            business_id         INT NOT NULL,
            customer_id         INT NOT NULL,
            call_id             INT NULL,

            -- What was booked
            service_name        VARCHAR(255) NOT NULL,
            service_code        VARCHAR(64) NULL,     -- optional FK to services.code later

            appointment_date    VARCHAR(32) NOT NULL, -- e.g. '2025-12-12'
            appointment_time    VARCHAR(32) NOT NULL, -- e.g. '16:00' or '4 PM'

            -- Booking meta
            booking_type        VARCHAR(32) NULL,     -- NEW, RESCHEDULE, CANCELLATION
            channel             VARCHAR(64) NOT NULL DEFAULT 'phone',   -- phone / web / walkin
            price_estimated     DECIMAL(10,2) NULL,  -- value of the booking
            currency            VARCHAR(16) NULL,    -- currency of the booking

            preferred_staff     VARCHAR(255) NULL,
            notes               TEXT NULL,

            status              VARCHAR(32) NOT NULL DEFAULT 'PENDING', -- PENDING / CONFIRMED / CANCELLED / NO_SHOW...
            source              VARCHAR(64) NOT NULL DEFAULT 'assistant', -- assistant / human

            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP
                ON UPDATE CURRENT_TIMESTAMP,

            CONSTRAINT fk_appointments_business
                FOREIGN KEY (business_id) REFERENCES businesses(id)
                ON DELETE CASCADE,
            CONSTRAINT fk_appointments_customer
                FOREIGN KEY (customer_id) REFERENCES customers(id)
                ON DELETE CASCADE,
            CONSTRAINT fk_appointments_call
                FOREIGN KEY (call_id) REFERENCES calls(id)
                ON DELETE SET NULL,

            INDEX idx_appointments_business_date (business_id, appointment_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """,
    ]

    with db_cursor() as cur:
        for ddl in ddl_create:
            cur.execute(ddl)


# ---------- BUSINESS / CUSTOMER HELPERS ----------

def get_or_create_business(profile: BusinessProfile, timezone: Optional[str] = None) -> int:
    """
    Ensure a row exists in `businesses` for the current profile.
    Returns business_id.
    """
    external_id = profile.id
    name = profile.name
    btype = profile.type
    tz = timezone

    with db_cursor() as cur:
        cur.execute(
            "SELECT id FROM businesses WHERE external_id = %s",
            (external_id,),
        )
        row = cur.fetchone()
        if row:
            return row["id"]

        cur.execute(
            """
            INSERT INTO businesses (external_id, name, type, timezone)
            VALUES (%s, %s, %s, %s)
            """,
            (external_id, name, btype, tz),
        )
        return cur.lastrowid


def get_or_create_customer(
    business_id: int,
    phone: str,
    name: Optional[str] = None,
) -> int:
    """
    Find or create a customer for (business_id, phone).
    """
    with db_cursor() as cur:
        cur.execute(
            "SELECT id FROM customers WHERE business_id = %s AND phone = %s",
            (business_id, phone),
        )
        row = cur.fetchone()
        if row:
            if name:
                cur.execute(
                    "UPDATE customers SET name = COALESCE(%s, name) WHERE id = %s",
                    (name, row["id"]),
                )
            return row["id"]

        cur.execute(
            """
            INSERT INTO customers (business_id, name, phone)
            VALUES (%s, %s, %s)
            """,
            (business_id, name, phone),
        )
        return cur.lastrowid


# ---------- CALL HELPERS ----------

def create_call(
    session_id: str,
    business_id: int,
    customer_id: Optional[int] = None,
    started_at: Optional[datetime] = None,
    raw_meta: Optional[str] = None,
    channel: str = "phone",
) -> int:
    """
    Create a call row and return call_id.
    """
    if started_at is None:
        started_at = datetime.utcnow()

    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO calls (
                session_id,
                business_id,
                customer_id,
                started_at,
                raw_meta,
                channel
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (session_id, business_id, customer_id, started_at, raw_meta, channel),
        )
        return cur.lastrowid


def end_call(
    call_id: int,
    ended_at: Optional[datetime] = None,
    outcome: Optional[str] = None,
    primary_intent: Optional[str] = None,
    primary_service: Optional[str] = None,
    total_duration_sec: Optional[int] = None,
    num_turns: Optional[int] = None,
    total_estimated_value: Optional[float] = None,
) -> None:
    """
    Update the call row at the end with analytics fields.
    """
    if ended_at is None:
        ended_at = datetime.utcnow()

    fields = ["ended_at = %s"]
    params: List[Any] = [ended_at]

    if outcome is not None:
        fields.append("outcome = %s")
        params.append(outcome)
    if primary_intent is not None:
        fields.append("primary_intent = %s")
        params.append(primary_intent)
    if primary_service is not None:
        fields.append("primary_service = %s")
        params.append(primary_service)
    if total_duration_sec is not None:
        fields.append("total_duration_sec = %s")
        params.append(total_duration_sec)
    if num_turns is not None:
        fields.append("num_turns = %s")
        params.append(num_turns)
    if total_estimated_value is not None:
        fields.append("total_estimated_value = %s")
        params.append(total_estimated_value)

    params.append(call_id)
    set_clause = ", ".join(fields)

    with db_cursor() as cur:
        cur.execute(
            f"UPDATE calls SET {set_clause} WHERE id = %s",
            tuple(params),
        )


def update_call_customer(call_id: int, customer_id: int) -> None:
    """
    Attach the resolved customer_id to a call (once we know them).
    """
    with db_cursor() as cur:
        cur.execute(
            "UPDATE calls SET customer_id = %s WHERE id = %s",
            (customer_id, call_id),
        )


def add_call_message(
    call_id: int,
    turn_index: int,
    role: str,
    text: str,
    intent: Optional[str],
    entities_json: Optional[str],
    timestamp: Optional[datetime] = None,
    service_name: Optional[str] = None,
    amount: Optional[float] = None,
    currency: Optional[str] = None,
    sentiment: Optional[str] = None,
) -> int:
    """
    Insert a message (user/assistant turn) into call_messages.
    Optional fields can be filled from NLU entities when available.
    """
    if timestamp is None:
        timestamp = datetime.utcnow()

    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO call_messages (
                call_id,
                turn_index,
                role,
                text,
                intent,
                service_name,
                amount,
                currency,
                sentiment,
                entities_json,
                timestamp
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                call_id,
                turn_index,
                role,
                text,
                intent,
                service_name,
                amount,
                currency,
                sentiment,
                entities_json,
                timestamp,
            ),
        )
        return cur.lastrowid


# ---------- APPOINTMENTS ----------

def create_appointment(
    business_id: int,
    customer_id: int,
    service_name: str,
    appointment_date: str,
    appointment_time: str,
    booking_type: Optional[str] = None,          # NEW / RESCHEDULE / CANCELLATION
    channel: str = "phone",
    price_estimated: Optional[float] = None,
    currency: Optional[str] = None,
    preferred_staff: Optional[str] = None,
    notes: Optional[str] = None,
    status: str = "PENDING",
    source: str = "assistant",                   # assistant / human
    call_id: Optional[int] = None,
    service_code: Optional[str] = None,          # optional normalized code
) -> int:
    """
    Create an appointment row.
    """
    now = datetime.utcnow()

    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO appointments (
                business_id,
                customer_id,
                call_id,
                service_name,
                service_code,
                appointment_date,
                appointment_time,
                booking_type,
                channel,
                price_estimated,
                currency,
                preferred_staff,
                notes,
                status,
                source,
                created_at,
                updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                business_id,
                customer_id,
                call_id,
                service_name,
                service_code,
                appointment_date,
                appointment_time,
                booking_type,
                channel,
                price_estimated,
                currency,
                preferred_staff,
                notes,
                status,
                source,
                now,
                now,
            ),
        )
        return cur.lastrowid


def list_appointments_for_business(
    business_id: int,
) -> List[Dict[str, Any]]:
    """
    Simple query: get all appointments for a business.
    """
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT *
            FROM appointments
            WHERE business_id = %s
            ORDER BY appointment_date, appointment_time
            """,
            (business_id,),
        )
        return cur.fetchall()