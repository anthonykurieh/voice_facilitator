import os
from contextlib import contextmanager
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

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


def init_db() -> None:
    ddl_create = [
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
        """
        CREATE TABLE IF NOT EXISTS services (
            id                   INT AUTO_INCREMENT PRIMARY KEY,
            business_id          INT NOT NULL,
            code                 VARCHAR(64) NOT NULL,
            name                 VARCHAR(255) NOT NULL,
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
        """
        CREATE TABLE IF NOT EXISTS calls (
            id                      INT AUTO_INCREMENT PRIMARY KEY,
            session_id              VARCHAR(255) NOT NULL UNIQUE,
            business_id             INT NOT NULL,
            customer_id             INT NULL,
            started_at              DATETIME NOT NULL,
            ended_at                DATETIME NULL,
            raw_meta                TEXT NULL,

            channel                 VARCHAR(64) NOT NULL DEFAULT 'phone',
            primary_intent          VARCHAR(255) NULL,
            primary_service         VARCHAR(255) NULL,
            outcome                 VARCHAR(64)  NULL,
            total_duration_sec      INT NULL,
            num_turns               INT NULL,
            total_estimated_value   DECIMAL(10,2) NULL,

            CONSTRAINT fk_calls_business
                FOREIGN KEY (business_id) REFERENCES businesses(id)
                ON DELETE CASCADE,
            CONSTRAINT fk_calls_customer
                FOREIGN KEY (customer_id) REFERENCES customers(id)
                ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """,
        """
        CREATE TABLE IF NOT EXISTS call_messages (
            id              INT AUTO_INCREMENT PRIMARY KEY,
            call_id         INT NOT NULL,
            turn_index      INT NOT NULL,
            role            VARCHAR(32) NOT NULL,
            text            TEXT NOT NULL,
            intent          VARCHAR(255) NULL,

            service_name    VARCHAR(255) NULL,
            amount          DECIMAL(10,2) NULL,
            currency        VARCHAR(16) NULL,
            sentiment       VARCHAR(16) NULL,
            entities_json   TEXT NULL,

            timestamp       DATETIME NOT NULL,

            CONSTRAINT fk_call_messages_call
                FOREIGN KEY (call_id) REFERENCES calls(id)
                ON DELETE CASCADE,
            INDEX idx_call_messages_call_id (call_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """,
        """
        CREATE TABLE IF NOT EXISTS appointments (
            id                  INT AUTO_INCREMENT PRIMARY KEY,
            business_id         INT NOT NULL,
            customer_id         INT NOT NULL,
            call_id             INT NULL,

            service_name        VARCHAR(255) NOT NULL,
            service_code        VARCHAR(64) NULL,

            appointment_date    VARCHAR(32) NOT NULL,
            appointment_time    VARCHAR(32) NOT NULL,

            booking_type        VARCHAR(32) NULL,
            channel             VARCHAR(64) NOT NULL DEFAULT 'phone',
            price_estimated     DECIMAL(10,2) NULL,
            currency            VARCHAR(16) NULL,

            preferred_staff     VARCHAR(255) NULL,
            notes               TEXT NULL,

            status              VARCHAR(32) NOT NULL DEFAULT 'PENDING',
            source              VARCHAR(64) NOT NULL DEFAULT 'assistant',

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


def get_or_create_business(profile: BusinessProfile, timezone: Optional[str] = None) -> int:
    external_id = profile.id
    name = profile.name
    btype = profile.type
    tz = timezone

    with db_cursor() as cur:
        cur.execute("SELECT id FROM businesses WHERE external_id = %s", (external_id,))
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


def get_or_create_customer(business_id: int, phone: str, name: Optional[str] = None) -> int:
    with db_cursor() as cur:
        cur.execute(
            "SELECT id, name FROM customers WHERE business_id = %s AND phone = %s",
            (business_id, phone),
        )
        row = cur.fetchone()
        if row:
            if name and (row.get("name") is None or row.get("name") == ""):
                cur.execute("UPDATE customers SET name = %s WHERE id = %s", (name, row["id"]))
            return row["id"]

        cur.execute(
            "INSERT INTO customers (business_id, name, phone) VALUES (%s, %s, %s)",
            (business_id, name, phone),
        )
        return cur.lastrowid


def create_call(
    session_id: str,
    business_id: int,
    customer_id: Optional[int] = None,
    started_at: Optional[datetime] = None,
    raw_meta: Optional[str] = None,
    channel: str = "phone",
) -> int:
    if started_at is None:
        started_at = datetime.utcnow()

    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO calls (session_id, business_id, customer_id, started_at, raw_meta, channel)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (session_id, business_id, customer_id, started_at, raw_meta, channel),
        )
        return cur.lastrowid


def update_call_customer(call_id: int, customer_id: int) -> None:
    with db_cursor() as cur:
        cur.execute("UPDATE calls SET customer_id = %s WHERE id = %s", (customer_id, call_id))


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
    if timestamp is None:
        timestamp = datetime.utcnow()

    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO call_messages (
                call_id, turn_index, role, text, intent,
                service_name, amount, currency, sentiment,
                entities_json, timestamp
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                call_id, turn_index, role, text, intent,
                service_name, amount, currency, sentiment,
                entities_json, timestamp
            ),
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
        cur.execute(f"UPDATE calls SET {set_clause} WHERE id = %s", tuple(params))


def create_appointment(
    business_id: int,
    customer_id: int,
    service_name: str,
    appointment_date: str,
    appointment_time: str,
    booking_type: Optional[str] = None,
    channel: str = "phone",
    price_estimated: Optional[float] = None,
    currency: Optional[str] = None,
    preferred_staff: Optional[str] = None,
    notes: Optional[str] = None,
    status: str = "PENDING",
    source: str = "assistant",
    call_id: Optional[int] = None,
    service_code: Optional[str] = None,
) -> int:
    now = datetime.utcnow()
    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO appointments (
                business_id, customer_id, call_id,
                service_name, service_code,
                appointment_date, appointment_time,
                booking_type, channel, price_estimated, currency,
                preferred_staff, notes, status, source, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                business_id, customer_id, call_id,
                service_name, service_code,
                appointment_date, appointment_time,
                booking_type, channel, price_estimated, currency,
                preferred_staff, notes, status, source, now, now
            ),
        )
        return cur.lastrowid


def update_appointment(
    appointment_id: int,
    *,
    service_name: Optional[str] = None,
    appointment_date: Optional[str] = None,
    appointment_time: Optional[str] = None,
    booking_type: Optional[str] = None,
    price_estimated: Optional[float] = None,
    currency: Optional[str] = None,
    preferred_staff: Optional[str] = None,
    notes: Optional[str] = None,
    status: Optional[str] = None,
) -> None:
    fields = []
    params: List[Any] = []

    if service_name is not None:
        fields.append("service_name = %s")
        params.append(service_name)
    if appointment_date is not None:
        fields.append("appointment_date = %s")
        params.append(appointment_date)
    if appointment_time is not None:
        fields.append("appointment_time = %s")
        params.append(appointment_time)
    if booking_type is not None:
        fields.append("booking_type = %s")
        params.append(booking_type)
    if price_estimated is not None:
        fields.append("price_estimated = %s")
        params.append(price_estimated)
    if currency is not None:
        fields.append("currency = %s")
        params.append(currency)
    if preferred_staff is not None:
        fields.append("preferred_staff = %s")
        params.append(preferred_staff)
    if notes is not None:
        fields.append("notes = %s")
        params.append(notes)
    if status is not None:
        fields.append("status = %s")
        params.append(status)

    if not fields:
        return

    set_clause = ", ".join(fields)
    params.append(appointment_id)

    with db_cursor() as cur:
        cur.execute(f"UPDATE appointments SET {set_clause} WHERE id = %s", tuple(params))


def list_appointments_for_business(business_id: int) -> List[Dict[str, Any]]:
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM appointments WHERE business_id = %s ORDER BY appointment_date, appointment_time",
            (business_id,),
        )
        return cur.fetchall()