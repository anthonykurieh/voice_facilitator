from app.backend.db import ensure_business, db_cursor


def seed_barber_business() -> int:
    business_id = ensure_business(
        slug="barber_demo",
        name="Downtown Barber Shop",
        timezone="Asia/Dubai",
    )

    with db_cursor() as cur:
        # staff
        cur.execute("SELECT COUNT(*) AS c FROM staff WHERE business_id=%s", (business_id,))
        if cur.fetchone()["c"] == 0:
            cur.execute(
                "INSERT INTO staff (business_id, name, specialty) VALUES "
                "(%s,'Omar','fades & skin fades'),"
                "(%s,'Karim','classic cuts & scissor work'),"
                "(%s,'Rami','beards & line-ups')",
                (business_id, business_id, business_id),
            )

        # services
        cur.execute("SELECT COUNT(*) AS c FROM services WHERE business_id=%s", (business_id,))
        if cur.fetchone()["c"] == 0:
            cur.execute(
                "INSERT INTO services (business_id, code, name, duration_min, price, currency) VALUES "
                "(%s,'haircut','Haircut',30,80,'AED'),"
                "(%s,'fade','Fade',45,120,'AED'),"
                "(%s,'beard','Beard Trim',20,60,'AED'),"
                "(%s,'combo','Haircut + Beard',50,150,'AED')",
                (business_id, business_id, business_id, business_id),
            )

        # business hours: Mon–Sat 10:00–20:00, Sun closed
        cur.execute("SELECT COUNT(*) AS c FROM business_hours WHERE business_id=%s", (business_id,))
        if cur.fetchone()["c"] == 0:
            for dow in range(0, 6):  # Mon..Sat
                cur.execute(
                    "INSERT INTO business_hours (business_id, dow, open_time, close_time, is_closed) "
                    "VALUES (%s,%s,'10:00','20:00',0)",
                    (business_id, dow),
                )
            cur.execute(
                "INSERT INTO business_hours (business_id, dow, open_time, close_time, is_closed) "
                "VALUES (%s,6,'00:00','00:00',1)",
                (business_id,),
            )

        # staff_services mapping (deterministic demo)
        cur.execute("SELECT id, code FROM services WHERE business_id=%s", (business_id,))
        services = {row["code"]: int(row["id"]) for row in cur.fetchall()}

        cur.execute("SELECT id, name FROM staff WHERE business_id=%s AND active=1", (business_id,))
        staff = {row["name"].lower(): int(row["id"]) for row in cur.fetchall()}

        cur.execute("DELETE FROM staff_services WHERE business_id=%s", (business_id,))

        # everyone can do haircut
        for sid in staff.values():
            cur.execute(
                "INSERT INTO staff_services (business_id, staff_id, service_id) VALUES (%s,%s,%s)",
                (business_id, sid, services["haircut"]),
            )

        # specialties
        cur.execute(
            "INSERT INTO staff_services (business_id, staff_id, service_id) VALUES (%s,%s,%s)",
            (business_id, staff["omar"], services["fade"]),
        )
        cur.execute(
            "INSERT INTO staff_services (business_id, staff_id, service_id) VALUES (%s,%s,%s)",
            (business_id, staff["rami"], services["beard"]),
        )
        cur.execute(
            "INSERT INTO staff_services (business_id, staff_id, service_id) VALUES (%s,%s,%s)",
            (business_id, staff["karim"], services["combo"]),
        )

    return business_id


def seed_all() -> int:
    return seed_barber_business()