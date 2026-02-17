"""Local HTML builder for creating business config YAML files.

Usage:
    python business_builder_server.py
Then open:
    http://127.0.0.1:8765
"""
import json
import os
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

import yaml


ROOT_DIR = Path(__file__).resolve().parent
WEB_DIR = ROOT_DIR / "web"
CONFIG_DIR = ROOT_DIR / "config"
HTML_PATH = WEB_DIR / "business_builder.html"
HOST = os.getenv("BUILDER_HOST", "127.0.0.1")
PORT = int(os.getenv("BUILDER_PORT", "8765"))


def _slugify(value: str) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "new_business"


def _safe_time(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if re.fullmatch(r"\d{2}:\d{2}", raw):
        return raw
    return ""


def _build_config(payload: dict) -> dict:
    business = payload.get("business", {})
    personality = payload.get("personality", {})
    booking = payload.get("booking", {})
    services = payload.get("services", [])
    staff = payload.get("staff", [])
    hours = payload.get("hours", {})
    email_theme = payload.get("email_theme", {})

    clean_services = []
    for row in services:
        name = (row.get("name") or "").strip()
        if not name:
            continue
        clean_services.append(
            {
                "name": name,
                "duration_minutes": int(row.get("duration_minutes") or 30),
                "price": float(row.get("price") or 0),
            }
        )

    clean_staff = []
    for row in staff:
        name = (row.get("name") or "").strip()
        if not name:
            continue
        clean_staff.append(
            {
                "name": name,
                "available": bool(row.get("available", True)),
                "email": (row.get("email") or "").strip(),
            }
        )

    clean_hours = {}
    for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"):
        day_data = hours.get(day, {})
        is_closed = bool(day_data.get("closed", False))
        open_time = _safe_time(day_data.get("open") or "")
        close_time = _safe_time(day_data.get("close") or "")
        clean_hours[day] = {
            "open": None if is_closed or not open_time else open_time,
            "close": None if is_closed or not close_time else close_time,
        }

    cfg = {
        "business": {
            "name": (business.get("name") or "").strip(),
            "type": (business.get("type") or "business").strip(),
            "phone": (business.get("phone") or "").strip(),
            "timezone": (business.get("timezone") or "America/New_York").strip(),
            "address": (business.get("address") or "").strip(),
        },
        "personality": {
            "tone": (personality.get("tone") or "friendly and professional").strip(),
            "greeting": (personality.get("greeting") or "Hello! Thank you for calling {business_name}. How can I help you today?").strip(),
        },
        "services": clean_services,
        "staff": clean_staff,
        "hours": clean_hours,
        "booking": {
            "advance_booking_days": int(booking.get("advance_booking_days") or 30),
            "minimum_notice_hours": int(booking.get("minimum_notice_hours") or 2),
            "buffer_between_appointments_minutes": int(booking.get("buffer_between_appointments_minutes") or 5),
        },
        "email_theme": {
            "palette_name": (email_theme.get("palette_name") or "Clean Slate").strip(),
            "hero_gradient_start": (email_theme.get("hero_gradient_start") or "#0f172a").strip(),
            "hero_gradient_end": (email_theme.get("hero_gradient_end") or "#1d4ed8").strip(),
            "accent": (email_theme.get("accent") or "#2563eb").strip(),
            "accent_soft": (email_theme.get("accent_soft") or "#dbeafe").strip(),
            "table_header_bg": (email_theme.get("table_header_bg") or "#f1f5f9").strip(),
            "table_header_text": (email_theme.get("table_header_text") or "#0f172a").strip(),
            "panel_bg": (email_theme.get("panel_bg") or "#f8fafc").strip(),
            "border": (email_theme.get("border") or "#e2e8f0").strip(),
        },
    }
    return cfg


class BuilderHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, data: dict):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status: int, text: str):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            if not HTML_PATH.exists():
                self._send_html(500, "<h1>Missing web/business_builder.html</h1>")
                return
            self._send_html(200, HTML_PATH.read_text(encoding="utf-8"))
            return
        self._send_json(404, {"error": "Not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/api/create-business":
            self._send_json(404, {"error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            payload = json.loads(raw.decode("utf-8"))
            cfg = _build_config(payload)

            business_name = cfg.get("business", {}).get("name", "").strip()
            if not business_name:
                self._send_json(400, {"error": "Business name is required."})
                return
            if not cfg.get("services"):
                self._send_json(400, {"error": "At least one service is required."})
                return
            if not cfg.get("staff"):
                self._send_json(400, {"error": "At least one staff member is required."})
                return

            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            base = f"business_config_{_slugify(business_name)}"
            path = CONFIG_DIR / f"{base}.yaml"
            idx = 2
            while path.exists():
                path = CONFIG_DIR / f"{base}_{idx}.yaml"
                idx += 1

            path.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
            self._send_json(
                200,
                {
                    "ok": True,
                    "message": "Business config created.",
                    "file": str(path.relative_to(ROOT_DIR)),
                },
            )
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})

    def log_message(self, format, *args):
        return


def main():
    server = HTTPServer((HOST, PORT), BuilderHandler)
    print(f"Business Builder running at http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
