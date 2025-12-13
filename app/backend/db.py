from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Dict, Optional

import pymysql

from app.config import (
    DB_HOST,
    DB_PORT,
    DB_USER,
    DB_PASSWORD,
    DB_NAME,
)


def get_connection():
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


@contextmanager
def db_cursor():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            yield cur
    finally:
        conn.close()


def init_db():
    """
    Creates all tables (idempotent).
    """
    with db_cursor() as cur:
        # businesses
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS businesses (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        # customers
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS customers (
                id INT AUTO_INCREMENT PRIMARY KEY,
                business_id INT NOT NULL,
                name VARCHAR(255) NOT NULL,
                phone VARCHAR(50) NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_customer_phone (business_id, phone),
                FOREIGN KEY (business_id) REFERENCES businesses(id) ON DELETE CASCADE
            );
            """
        )

        # calls
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS calls (
                id INT AUTO_INCREMENT PRIMARY KEY,
                session_id VARCHAR(64) NOT NULL,
                business_id INT NULL,
                customer_id INT NULL,
                started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                ended_at DATETIME NULL,
                FOREIGN KEY (business_id) REFERENCES businesses(id) ON DELETE SET NULL,
                FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE SET NULL
            );
            """
        )

        # call_messages
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS call_messages (
                id INT AUTO_INCREMENT PRIMARY KEY,
                call_id INT NOT NULL,
                role ENUM('user','assistant','system') NOT NULL,
                content TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (call_id) REFERENCES calls(id) ON DELETE CASCADE
            );
            """
        )

        # services
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS services (
                id INT AUTO_INCREMENT PRIMARY KEY,
                business_id INT NOT NULL,
                name VARCHAR(255) NOT NULL,
                default_duration_min INT NOT NULL DEFAULT 30,
                base_price DECIMAL(10,2) NULL,
                currency VARCHAR(8) NULL,
                is_active TINYINT(1) DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_service_name (business_id, name),
                FOREIGN KEY (business_id) REFERENCES businesses(id) ON DELETE CASCADE
            );
            """
        )

        # staff
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS staff (
                id INT AUTO_INCREMENT PRIMARY KEY,
                business_id INT NOT NULL,
                name VARCHAR(255) NOT NULL,
                specialization VARCHAR(255) NULL,
                is_active TINYINT(1) DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (business_id) REFERENCES businesses(id) ON DELETE CASCADE
            );
            """
        )

        # staff_working_hours
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS staff_working_hours (
                id INT AUTO_INCREMENT PRIMARY KEY,
                staff_id INT NOT NULL,
                day_of_week INT NOT NULL,  -- 0=Mon..6=Sun
                start_time TIME NOT NULL,
                end_time TIME NOT NULL,
                FOREIGN KEY (staff_id) REFERENCES staff(id) ON DELETE CASCADE,
                UNIQUE KEY uq_staff_day (staff_id, day_of_week)
            );
            """
        )

        # appointments
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS appointments (
                id INT AUTO_INCREMENT PRIMARY KEY,
                business_id INT NOT NULL,
                customer_id INT NOT NULL,
                service_name VARCHAR(255) NOT NULL,
                booking_type VARCHAR(64) NOT NULL DEFAULT 'appointment',
                appointment_date DATE NOT NULL,
                appointment_time VARCHAR(5) NOT NULL,  -- "HH:MM"
                staff_id INT NULL,
                duration_min INT NULL,
                price DECIMAL(10,2) NULL,
                currency VARCHAR(8) NULL,
                status ENUM('PENDING','CONFIRMED','CANCELLED') NOT NULL DEFAULT 'PENDING',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (business_id) REFERENCES businesses(id) ON DELETE CASCADE,
                FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
                FOREIGN KEY (staff_id) REFERENCES staff(id) ON DELETE SET NULL
            );
            """
        )


def log_call_start(session_id: str, business_id: Optional[int] = None, customer_id: Optional[int] = None) -> int:
    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO calls (session_id, business_id, customer_id)
            VALUES (%s, %s, %s)
            """,
            (session_id, business_id, customer_id),
        )
        return int(cur.lastrowid)


def log_call_end(call_id: int) -> None:
    with db_cursor() as cur:
        cur.execute(
            "UPDATE calls SET ended_at=NOW() WHERE id=%s",
            (call_id,),
        )


def log_message(call_id: int, role: str, content: str) -> None:
    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO call_messages (call_id, role, content)
            VALUES (%s, %s, %s)
            """,
            (call_id, role, content),
        )


def upsert_customer(business_id: int, name: str, phone: str) -> int:
    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO customers (business_id, name, phone)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE name=VALUES(name)
            """,
            (business_id, name, phone),
        )

        # Fetch ID
        cur.execute(
            "SELECT id FROM customers WHERE business_id=%s AND phone=%s",
            (business_id, phone),
        )
        row = cur.fetchone()
        return int(row["id"])


def create_appointment(
    business_id: int,
    customer_id: int,
    service_name: str,
    booking_type: str,
    appointment_date: str,
    appointment_time: str,
    staff_id: Optional[int] = None,
) -> int:
    """
    Creates appointment. Duration + price pulled from services table when available.
    """
    with db_cursor() as cur:
        cur.execute(
            """
            SELECT default_duration_min, base_price, currency
            FROM services
            WHERE business_id=%s AND LOWER(name)=LOWER(%s) AND is_active=1
            """,
            (business_id, service_name),
        )
        svc = cur.fetchone() or {}
        duration = svc.get("default_duration_min") or 30
        price = svc.get("base_price")
        currency = svc.get("currency")

        cur.execute(
            """
            INSERT INTO appointments
            (business_id, customer_id, service_name, booking_type, appointment_date, appointment_time, staff_id, duration_min, price, currency, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'CONFIRMED')
            """,
            (business_id, customer_id, service_name, booking_type, appointment_date, appointment_time, staff_id, duration, price, currency),
        )
        return int(cur.lastrowid)