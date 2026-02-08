"""Dash-based dashboard for Voice Facilitator data.

Usage:
    pip install dash pandas plotly sqlalchemy pymysql python-dotenv
    python dashboard_dash.py

The app will listen on http://127.0.0.1:8050 by default.
"""
import calendar as cal
import json
import os
from datetime import date, datetime, time, timedelta
from typing import Optional

# Stub bottleneck if compiled wheel conflicts with NumPy >=2
import types
import sys
# Stub bottleneck with a compatible version string to silence pandas optional dependency warning
sys.modules.setdefault("bottleneck", types.SimpleNamespace(__name__="bottleneck", __version__="1.3.6"))

# Avoid loading bottleneck compiled against incompatible NumPy
os.environ.setdefault("PANDAS_NO_BOTTLENECK", "1")

import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from dash import Dash, dcc, html, callback_context
from dash.dependencies import Input, Output, State, ALL
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.engine import Engine
from src.analytics_agent import AnalyticsAgent

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def get_engine() -> Engine:
    host = os.getenv("DB_HOST") or os.getenv("MYSQL_HOST", "localhost")
    port = os.getenv("DB_PORT") or os.getenv("MYSQL_PORT", "3306")
    user = os.getenv("DB_USER") or os.getenv("MYSQL_USER", "root")
    password = os.getenv("DB_PASSWORD") or os.getenv("MYSQL_PASSWORD", "")
    db = os.getenv("DB_NAME") or os.getenv("MYSQL_DATABASE", "voice_assistant")
    return create_engine(f"mysql+pymysql://{user}:{password}@{host}:{port}/{db}")


def safe_execute(engine: Engine, sql: str):
    sql_lower = sql.lower()
    if not sql_lower.strip().startswith("select"):
        raise ValueError("Only SELECT queries are allowed")
    banned = ["insert", "update", "delete", "alter", "drop", "truncate", "create"]
    if any(b in sql_lower for b in banned):
        raise ValueError("Unsafe SQL detected")
    if "limit" not in sql_lower:
        sql = sql.rstrip("; ") + " LIMIT 200"
    with engine.connect() as conn:
        df = pd.read_sql(text(sql), conn)
        return df.to_dict(orient="records")


def fetch_df(engine: Engine, query: str, params=None):
    if isinstance(params, list):
        params = tuple(params)
    return pd.read_sql(query, con=engine, params=params)


def time_delta_minutes(value):
    if pd.isna(value):
        return 0
    if isinstance(value, str):
        try:
            return pd.to_timedelta(value).total_seconds() / 60
        except ValueError:
            return 0
    if isinstance(value, datetime):
        return value.hour * 60 + value.minute + value.second / 60
    if hasattr(value, "total_seconds"):
        return value.total_seconds() / 60
    if hasattr(value, "hour"):
        return value.hour * 60 + value.minute + getattr(value, "second", 0) / 60
    return 0


def coerce_time_value(value):
    if pd.isna(value):
        return pd.NaT
    if isinstance(value, datetime):
        return value
    if isinstance(value, time):
        return datetime.combine(date(1900, 1, 1), value)
    if isinstance(value, (pd.Timedelta, np.timedelta64)):
        td = pd.to_timedelta(value, errors="coerce")
        if pd.isna(td):
            return pd.NaT
        return datetime(1900, 1, 1) + td
    if isinstance(value, str) and "day" in value:
        td = pd.to_timedelta(value, errors="coerce")
        if pd.notna(td):
            return datetime(1900, 1, 1) + td
    try:
        return pd.to_datetime(value, errors="coerce")
    except Exception:
        return pd.NaT


def load_data(engine: Engine, start: Optional[date] = None, end: Optional[date] = None):
    today = date.today()
    start = start or (today - timedelta(days=89))
    end = end or today
    appts = fetch_df(
        engine,
        """
        SELECT a.id, a.appointment_date, a.appointment_time, a.duration_minutes, a.status,
               a.created_at, a.staff_id, a.service_id,
               s.name AS service_name, s.price AS service_price, s.duration_minutes AS service_duration,
               st.name AS staff_name
        FROM appointments a
        LEFT JOIN services s ON a.service_id = s.id
        LEFT JOIN staff st ON a.staff_id = st.id
        WHERE a.appointment_date BETWEEN %s AND %s
        """,
        [start, end],
    )
    kpis = fetch_df(
        engine,
        """
        SELECT event_type, service_id, service_name, service_price, staff_id, staff_name,
               duration_minutes, appointment_date, appointment_time, created_at
        FROM kpi_events
        WHERE appointment_date BETWEEN %s AND %s
        """,
        [start, end],
    )
    calls = fetch_df(
        engine,
        "SELECT id, started_at, ended_at, outcome FROM calls WHERE DATE(started_at) BETWEEN %s AND %s",
        [start, end],
    )
    hours = fetch_df(
        engine,
        "SELECT day_of_week, open_time, close_time, is_closed FROM business_hours WHERE business_id = 1",
    )
    return appts, calls, hours, kpis


def compute_metrics(appts, calls, hours, kpis):
    appts = appts.copy()  # avoid chained assignment warnings
    kpis = kpis.copy()
    calls = calls.copy()
    today = date.today()
    last_7 = today - timedelta(days=6)
    prev_7_start = last_7 - timedelta(days=7)
    prev_7_end = last_7 - timedelta(days=1)

    def window_count(start, end, df, col="appointment_date", status=None):
        mask = (df[col].dt.date >= start) & (df[col].dt.date <= end)
        if status:
            mask &= df["status"] == status
        return df.loc[mask].shape[0]

    appts["appointment_date"] = pd.to_datetime(appts["appointment_date"])
    appts["appointment_time"] = appts["appointment_time"].apply(coerce_time_value)
    appts["created_at"] = pd.to_datetime(appts["created_at"], errors="coerce")
    kpis["created_at"] = pd.to_datetime(kpis.get("created_at"))
    if "appointment_date" in kpis.columns:
        kpis["appointment_date"] = pd.to_datetime(kpis["appointment_date"], errors="coerce")
    if "appointment_time" in kpis.columns:
        kpis["appointment_time"] = kpis["appointment_time"].apply(coerce_time_value)

    bookings_7 = window_count(last_7, today, appts, status="scheduled")
    bookings_prev = window_count(prev_7_start, prev_7_end, appts, status="scheduled")

    conversion = None
    if not calls.empty:
        calls["started_at"] = pd.to_datetime(calls.get("started_at"), errors="coerce")
        calls_booked = calls[calls["outcome"] == "booked"].shape[0]
        conversion = (calls_booked / max(len(calls), 1)) * 100

    # Simple call funnel metrics (last 7 days)
    total_calls_7 = 0
    booked_calls_pct_7 = 0
    if not calls.empty and "started_at" in calls.columns:
        calls_7 = calls[calls["started_at"].dt.date >= last_7]
        total_calls_7 = int(calls_7.shape[0])
        booked_calls_7 = int(calls_7[calls_7["outcome"] == "booked"].shape[0])
        booked_calls_pct_7 = (booked_calls_7 / max(total_calls_7, 1)) * 100

    appts["service_price"] = pd.to_numeric(appts["service_price"], errors="coerce").fillna(0)
    appts["service_name"] = appts["service_name"].fillna("Unknown")
    appts["staff_name"] = appts["staff_name"].fillna("Unassigned")

    # Revenue from kpi_events (booked) to avoid null service_ids
    revenue_30 = 0
    avg_booking_value_30 = 0
    if not kpis.empty:
        kpi_booked = kpis[kpis["event_type"] == "booked"].copy()
        kpi_booked["service_price"] = pd.to_numeric(kpi_booked["service_price"], errors="coerce").fillna(0)
        booked_30 = kpi_booked[
            kpi_booked["created_at"].dt.date >= today - timedelta(days=29)
        ]
        revenue_30 = booked_30["service_price"].sum()
        avg_booking_value_30 = revenue_30 / max(booked_30.shape[0], 1)

    total_recent = appts[appts["appointment_date"].dt.date >= today - timedelta(days=29)]
    total_appointments_30 = int(total_recent.shape[0])
    no_show_rate = (
        total_recent[total_recent["status"] == "no_show"].shape[0]
        / max(total_recent.shape[0], 1)
        * 100
    )
    cancel_rate = (
        total_recent[total_recent["status"] == "cancelled"].shape[0]
        / max(total_recent.shape[0], 1)
        * 100
    )

    hours["open_minutes"] = hours.apply(
        lambda row: 0
        if row["is_closed"]
        else time_delta_minutes(row["close_time"]) - time_delta_minutes(row["open_time"]),
        axis=1,
    )
    dow_to_minutes = dict(zip(hours["day_of_week"], hours["open_minutes"]))
    appts["dow"] = appts["appointment_date"].dt.dayofweek
    appts["open_minutes"] = appts["dow"].map(dow_to_minutes).fillna(0)
    daily = (
        appts.groupby(appts["appointment_date"].dt.date)
        .agg(duration_sum=("duration_minutes", "sum"), open_sum=("open_minutes", "sum"))
        .reset_index()
    )
    daily["utilization"] = daily.apply(
        lambda row: (row["duration_sum"] / row["open_sum"] * 100) if row["open_sum"] else 0,
        axis=1,
    )
    utilization_avg = daily["utilization"].mean() if not daily.empty else 0

    appts_for_charts = appts[appts["status"].isin(["scheduled", "completed"])].copy()

    ts = (
        appts_for_charts.groupby(appts_for_charts["appointment_date"].dt.date)
        .agg(bookings=("id", "count"), revenue=("service_price", "sum"))
        .reset_index()
        .rename(columns={"appointment_date": "date"})
        .sort_values("date")
    )
    ts["bookings_ma7"] = ts["bookings"].rolling(7, min_periods=1).mean()

    if not kpis.empty:
        kpi_booked = kpis[kpis["event_type"] == "booked"].copy()
        kpi_booked["service_name"] = kpi_booked["service_name"].fillna("Unknown")
        kpi_booked["staff_name"] = kpi_booked["staff_name"].fillna("Unassigned")
        kpi_booked["service_price"] = pd.to_numeric(kpi_booked["service_price"], errors="coerce").fillna(0)
        svc = (
            kpi_booked.groupby("service_name")
            .agg(count=("service_name", "count"), revenue=("service_price", "sum"))
            .reset_index()
            .sort_values("count", ascending=False)
        )
        staff = (
            kpi_booked.groupby("staff_name")
            .agg(duration=("duration_minutes", "sum"), bookings=("staff_name", "count"), revenue=("service_price", "sum"))
            .reset_index()
            .sort_values("duration", ascending=False)
        )
    else:
        svc = (
            appts_for_charts.groupby("service_name")
            .agg(count=("id", "count"), revenue=("service_price", "sum"))
            .reset_index()
            .sort_values("count", ascending=False)
        )
        staff = (
            appts_for_charts.groupby("staff_name")
            .agg(duration=("duration_minutes", "sum"), bookings=("id", "count"), revenue=("service_price", "sum"))
            .reset_index()
            .sort_values("duration", ascending=False)
        )

    # For pie charts: service/staff share (booked events preferred)
    svc_pie = svc.copy()
    if "revenue" in svc_pie.columns and svc_pie["revenue"].sum() > 0:
        svc_pie_value_field = "revenue"
    else:
        svc_pie_value_field = "count"
    staff_pie = staff.copy()
    if "duration" in staff_pie.columns and staff_pie["duration"].sum() > 0:
        staff_pie_value_field = "duration"
    else:
        staff_pie_value_field = "bookings"

    return {
        "bookings_7": bookings_7,
        "bookings_prev": bookings_prev,
        "conversion": conversion,
        "total_calls_7": total_calls_7,
        "booked_calls_pct_7": booked_calls_pct_7,
        "revenue_30": revenue_30,
        "avg_booking_value_30": avg_booking_value_30,
        "total_appointments_30": total_appointments_30,
        "no_show_rate": no_show_rate,
        "cancel_rate": cancel_rate,
        "utilization_avg": utilization_avg,
        "ts": ts,
        "svc": svc,
        "staff": staff,
        "appts": appts_for_charts,
        "kpis": kpis,
        "svc_pie": svc_pie,
        "svc_pie_value": svc_pie_value_field,
        "staff_pie": staff_pie,
        "staff_pie_value": staff_pie_value_field,
    }


def format_metric(value, suffix=""):
    return f"{value:.1f}{suffix}" if isinstance(value, float) else str(value)


def build_figures(metrics):
    ts = metrics["ts"]
    fig_bookings = go.Figure()
    fig_bookings.add_trace(go.Scatter(x=ts["date"], y=ts["bookings"], mode="lines+markers", name="Bookings"))
    fig_bookings.add_trace(go.Scatter(x=ts["date"], y=ts["bookings_ma7"], mode="lines", name="7d MA"))
    fig_bookings.update_layout(title="Daily Bookings", margin=dict(t=40))

    fig_revenue = go.Figure()
    fig_revenue.add_trace(go.Bar(x=ts["date"], y=ts["revenue"], name="Revenue"))
    fig_revenue.update_layout(title="Revenue", margin=dict(t=40))

    svc = metrics["svc"]
    fig_svc = go.Figure()
    fig_svc.add_trace(go.Bar(x=svc["service_name"], y=svc["count"], hovertext=svc["revenue"], name="Count"))
    fig_svc.update_layout(title="Service Mix (count)", xaxis_title="Service", yaxis_title="Bookings")

    staff = metrics["staff"]
    fig_staff = go.Figure()
    fig_staff.add_trace(go.Bar(x=staff["staff_name"], y=staff["duration"], hovertext=staff["revenue"], name="Minutes"))
    fig_staff.update_layout(title="Staff Load (minutes)", xaxis_title="Staff", yaxis_title="Minutes")

    # Pie charts
    svc_pie = metrics["svc_pie"]
    svc_value_field = metrics["svc_pie_value"]
    fig_svc_pie = go.Figure(go.Pie(labels=svc_pie["service_name"], values=svc_pie[svc_value_field], hole=0.35))
    fig_svc_pie.update_layout(title=f"Service Mix ({svc_value_field})")

    staff_pie = metrics["staff_pie"]
    staff_value_field = metrics["staff_pie_value"]
    fig_staff_pie = go.Figure(go.Pie(labels=staff_pie["staff_name"], values=staff_pie[staff_value_field], hole=0.35))
    fig_staff_pie.update_layout(title=f"Staff Share ({staff_value_field})")

    return fig_bookings, fig_revenue, fig_svc, fig_staff, fig_svc_pie, fig_staff_pie


def _normalize_calendar_appts(appts):
    appts = appts.copy()
    appts["appointment_date"] = pd.to_datetime(appts["appointment_date"], errors="coerce")
    appts["appointment_time"] = appts["appointment_time"].apply(coerce_time_value)
    appts["staff_name"] = appts["staff_name"].fillna("Unassigned")
    appts["service_name"] = appts["service_name"].fillna("Service")
    appts["service_name"] = appts["service_name"].replace({"Unknown": "Service"})
    appts["status"] = appts["status"].fillna("scheduled")
    duration = pd.to_numeric(appts["duration_minutes"], errors="coerce")
    if "service_duration" in appts.columns:
        duration = duration.fillna(pd.to_numeric(appts["service_duration"], errors="coerce"))
    appts["duration_minutes"] = duration.fillna(30)
    appts["event_id"] = appts.apply(
        lambda row: str(row["id"]) if pd.notna(row.get("id")) else f"row-{row.name}",
        axis=1,
    )
    return appts


def build_time_options():
    options = [{"label": "All day", "value": ""}]
    start_hour = 6
    end_hour = 21
    for hour in range(start_hour, end_hour + 1):
        for minute in (0, 30):
            label = datetime(2000, 1, 1, hour, minute).strftime("%I:%M %p").lstrip("0")
            value = f"{hour:02d}:{minute:02d}"
            options.append({"label": label, "value": value})
    return options


def _format_time(value):
    if pd.isna(value):
        return "TBD"
    try:
        return pd.to_datetime(value).strftime("%I:%M %p").lstrip("0")
    except Exception:
        return "TBD"


def build_month_view(appts):
    if appts.empty:
        return html.Div("No appointments available.", className="calendar-empty")

    appts = _normalize_calendar_appts(appts)
    today = date.today()
    year = today.year
    month = today.month
    appts = appts[appts["appointment_date"].dt.month == month]
    appts = appts[appts["appointment_date"].dt.year == year]
    appts = appts[appts["status"].isin(["scheduled", "completed"])]
    appts = appts.sort_values("appointment_time")

    day_events = {}
    for _, row in appts.iterrows():
        day_key = row["appointment_date"].date()
        label = f"{_format_time(row['appointment_time'])} {row['service_name']}"
        day_events.setdefault(day_key, []).append(
            {"label": label, "event_id": row["event_id"]}
        )

    weeks = cal.monthcalendar(year, month)
    head = html.Div(
        [html.Div(day, className="cal-head") for day in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]],
        className="cal-head-row",
    )
    cells = []
    for week in weeks:
        for day in week:
            if day == 0:
                cells.append(html.Div("", className="cal-cell cal-empty"))
                continue
            day_date = date(year, month, day)
            events = day_events.get(day_date, [])
            chips = [
                html.Button(
                    evt["label"],
                    id={"type": "cal-event", "index": evt["event_id"]},
                    n_clicks=0,
                    className="cal-chip-btn",
                )
                for evt in events[:2]
            ]
            more_count = len(events) - len(chips)
            if more_count > 0:
                chips.append(html.Div(f"+{more_count} more", className="cal-more"))
            cells.append(html.Div([
                html.Div(str(day), className="cal-day"),
                html.Div(chips, className="cal-events"),
            ], className="cal-cell"))
    return html.Div([
        html.Div(f"{cal.month_name[month]} {year}", className="calendar-title"),
        head,
        html.Div(cells, className="calendar-grid"),
    ], className="calendar-wrap")


def build_day_view(appts, selected_date=None, selected_time=None):
    if selected_date:
        target_date = pd.to_datetime(selected_date).date()
    else:
        target_date = date.today()
    if appts.empty:
        return html.Div("No appointments available.", className="calendar-empty")

    appts = _normalize_calendar_appts(appts)
    appts = appts[appts["appointment_date"].dt.date == target_date].copy()
    if selected_time:
        try:
            time_value = datetime.strptime(selected_time, "%H:%M").time()
            appts = appts[
                (appts["appointment_time"].dt.hour == time_value.hour)
                & (appts["appointment_time"].dt.minute == time_value.minute)
            ]
        except ValueError:
            pass
    if appts.empty:
        return html.Div("No appointments scheduled.", className="calendar-empty")

    appts["start_time"] = appts["appointment_time"].dt.strftime("%I:%M %p").fillna("TBD")
    appts = appts.sort_values("appointment_time")
    items = []
    for _, row in appts.iterrows():
        items.append(html.Button([
            html.Div(row["start_time"], className="day-time"),
            html.Div([
                html.Div(row["service_name"], className="day-title"),
                html.Div(f"{row['staff_name']} • {row['status']}", className="day-subtitle"),
            ], className="day-details"),
        ], id={"type": "cal-event", "index": row["event_id"]}, n_clicks=0, className="day-item-btn"))
    return html.Div(items, className="day-list")


def build_calendar_views(metrics):
    appts = metrics["appts"].copy()
    if appts.empty:
        empty_fig = go.Figure()
        empty_fig.add_annotation(text="No appointments in range", showarrow=False, x=0.5, y=0.5, xref="paper", yref="paper")
        empty_fig.update_layout(title="Appointments Timeline", margin=dict(t=40))
        month_view = html.Div("No appointments available.", className="calendar-empty")
        day_view = html.Div("No appointments available.", className="calendar-empty")
        return empty_fig, month_view, day_view

    appts = _normalize_calendar_appts(appts)
    date_str = appts["appointment_date"].dt.date.astype(str)
    time_str = appts["appointment_time"].dt.time.astype(str)
    start = pd.to_datetime(date_str + " " + time_str, errors="coerce")
    start = start.fillna(pd.to_datetime(appts["appointment_date"].dt.date))
    appts["start"] = start
    appts["end"] = appts["start"] + pd.to_timedelta(appts["duration_minutes"], unit="m")

    timeline_df = appts[appts["status"].isin(["scheduled", "completed"])].copy()
    if timeline_df.empty:
        fig = go.Figure()
        fig.add_annotation(text="No scheduled or completed appointments.", showarrow=False, x=0.5, y=0.5, xref="paper", yref="paper")
        fig.update_layout(title="Appointments Timeline", margin=dict(t=40))
    else:
        fig = px.timeline(
            timeline_df,
            x_start="start",
            x_end="end",
            y="staff_name",
            color="status",
            hover_data=["service_name"],
        )
        fig.update_yaxes(autorange="reversed")
        fig.update_layout(title="Appointments Timeline", margin=dict(t=40))

    month_view = build_month_view(appts)
    day_view = build_day_view(appts, date.today())
    return fig, month_view, day_view


def build_calendar_payload(appts):
    appts = _normalize_calendar_appts(appts)
    payload = appts[[
        "event_id",
        "id",
        "appointment_date",
        "appointment_time",
        "duration_minutes",
        "status",
        "service_name",
        "staff_name",
        "service_duration",
    ]].copy()
    payload["appointment_date"] = appts["appointment_date"].dt.date.astype(str)
    payload["appointment_time"] = appts["appointment_time"].dt.strftime("%H:%M:%S")
    payload = payload.replace({np.nan: None})
    return payload


def serve_layout(metrics, figs, start_date=None, end_date=None):
    bookings_delta = ((metrics["bookings_7"] - metrics["bookings_prev"]) / max(metrics["bookings_prev"], 1)) * 100
    cards = [
        {"label": "Bookings (7d)", "value": metrics["bookings_7"], "delta": f"{bookings_delta:.1f}% vs prev 7d"},
        {"label": "Total Calls (7d)", "value": metrics["total_calls_7"], "delta": ""},
        {"label": "Booked Calls % (7d)", "value": f"{metrics['booked_calls_pct_7']:.1f}%", "delta": ""},
        {"label": "Cancellation Rate (30d)", "value": f"{metrics['cancel_rate']:.1f}%", "delta": ""},
        {"label": "Average Booking Value (30d)", "value": f"${metrics['avg_booking_value_30']:.0f}", "delta": ""},
        {"label": "Total Appointments (30d)", "value": metrics["total_appointments_30"], "delta": ""},
        {"label": "Conversion", "value": f"{metrics['conversion']:.1f}%" if metrics["conversion"] is not None else "-", "delta": ""},
        {"label": "Revenue (30d)", "value": f"${metrics['revenue_30']:.0f}", "delta": ""},
    ]

    card_divs = [
        html.Div([
            html.Div(card["label"], className="kpi-label"),
            html.Div(card["value"], className="kpi-value"),
            html.Div(card["delta"], className="kpi-delta"),
        ], className="card kpi-card") for card in cards
    ]

    return html.Div([
        html.Div([
            html.Div([
                html.Div("Overview", className="section-eyebrow"),
                html.H2("Business Dashboard", className="section-title"),
                html.Div("Performance snapshot across bookings, revenue, and staffing.", className="section-subtitle"),
            ], className="section-head"),
            html.Div(card_divs, className="cards"),
        ], className="section-block"),
        html.Div([
            html.Div(dcc.Graph(figure=figs[0], className="graph-card"), className="panel-graph"),
            html.Div(dcc.Graph(figure=figs[1], className="graph-card"), className="panel-graph"),
        ], className="row"),
        html.Div([
            html.Div(dcc.Graph(figure=figs[2], className="graph-card"), className="panel-graph"),
            html.Div(dcc.Graph(figure=figs[3], className="graph-card"), className="panel-graph"),
        ], className="row"),
        html.Div([
            html.Div(dcc.Graph(figure=figs[4], className="graph-card"), className="panel-graph"),
            html.Div(dcc.Graph(figure=figs[5], className="graph-card"), className="panel-graph"),
        ], className="row"),
    ], className="dashboard-stack")


def build_app(metrics, figs):
    app = Dash(__name__, external_stylesheets=["https://cdnjs.cloudflare.com/ajax/libs/normalize/8.0.1/normalize.min.css"])
    app.title = "Voice Facilitator Dashboard"

    today = date.today()
    default_start = today - timedelta(days=89)

    graph_tab = html.Div([
        html.Div([
            html.Div([
                html.Div("Date range", className="pill-label"),
                dcc.DatePickerRange(
                    id="date-range",
                    start_date=default_start,
                    end_date=today,
                    display_format="YYYY-MM-DD",
                ),
            ], className="card"),
        ], className="cards"),
        html.Div(id="dashboard-panel", children=serve_layout(metrics, figs), className="stack")
    ], className="tab-content")
    qa_tab = html.Div([
        html.Div([
            html.H3("Conversational Analytics", className="section-title"),
            html.Div("Ask a question about your business. We’ll translate to safe SQL and summarize.", className="subtitle"),
        ], className="section-head"),
        html.Div([
            dcc.Textarea(
                id="qa-input",
                placeholder="e.g., How many bookings last week? Revenue by staff this month?",
                className="textarea",
            ),
            html.Button("Ask", id="qa-submit", n_clicks=0, className="primary-btn"),
        ], className="stack"),
        html.Div([
            html.Div("Answer", className="pill-label"),
            html.Div(id="qa-answer", className="answer-card")
        ], className="card shadow"),
        html.Div([
            html.Div("SQL", className="pill-label"),
            html.Pre(id="qa-sql", className="code-block")
        ], className="card shadow")
    ], className="panel stack tab-content")

    calendar_timeline, calendar_month, calendar_day = build_calendar_views(metrics)
    calendar_payload = build_calendar_payload(metrics["appts"])
    calendar_data = calendar_payload.to_dict(orient="records")
    time_options = build_time_options()
    calendar_tab = html.Div([
        html.Div([
            html.H3("Calendar", className="section-title"),
            html.Div("View upcoming appointments in timeline or month format.", className="subtitle"),
        ], className="section-head"),
        dcc.Store(id="calendar-data", data=calendar_data),
        dcc.Tabs([
            dcc.Tab(label="Timeline", children=[dcc.Graph(figure=calendar_timeline, className="graph-card")], className="tab", selected_className="tab-selected"),
            dcc.Tab(label="Month", children=[calendar_month], className="tab", selected_className="tab-selected"),
            dcc.Tab(label="Day", children=[
                html.Div([
                    html.Div("Select day", className="pill-label"),
                    dcc.DatePickerSingle(id="calendar-day", date=date.today(), display_format="YYYY-MM-DD"),
                    dcc.Dropdown(id="calendar-time", options=time_options, value="", clearable=False, className="calendar-time"),
                ], className="calendar-toolbar"),
                html.Div(id="calendar-day-view", children=calendar_day),
            ], className="tab", selected_className="tab-selected"),
        ], className="subtabs"),
        html.Div("Select an event to see details.", id="calendar-detail", className="calendar-detail")
    ], className="panel stack tab-content")

    app.layout = html.Div([
        html.Div([
            html.Div([
                html.Div("Voice Facilitator", className="eyebrow"),
                html.H1("Operations Command Center", className="hero-title"),
                html.Div("Sleek, fast visibility into scheduling, revenue, and customer momentum.", className="hero-subtitle"),
            ], className="hero-copy"),
            html.Div([
                html.Div("Last synced", className="pill-label"),
                html.Div(datetime.now().strftime("%b %d, %Y • %I:%M %p").lstrip("0"), className="hero-meta"),
            ], className="hero-meta-wrap"),
        ], className="hero"),
        dcc.Tabs([
            dcc.Tab(label="Dashboard", children=[graph_tab], className="tab", selected_className="tab-selected"),
            dcc.Tab(label="Calendar", children=[calendar_tab], className="tab", selected_className="tab-selected"),
            dcc.Tab(label="Conversational Analytics", children=[qa_tab], className="tab", selected_className="tab-selected"),
        ], className="tabs")
    ], className="page")

    app.index_string = """
    <!DOCTYPE html>
    <html>
        <head>
            {%metas%}
            <title>{%title%}</title>
            {%favicon%}
            {%css%}
            <style>
                @import url('https://fonts.googleapis.com/css2?family=Fraunces:wght@600;700&family=Space+Grotesk:wght@400;500;600;700&display=swap');
                :root {
                    --bg: #f4f2ef;
                    --panel: rgba(255, 255, 255, 0.92);
                    --card: rgba(255, 255, 255, 0.96);
                    --accent: #0f6fff;
                    --accent-strong: #0b1b3a;
                    --accent2: #1bb98a;
                    --text: #111827;
                    --muted: #6b7280;
                    --shadow: 0 20px 45px rgba(15, 23, 42, 0.12);
                    --border: rgba(15, 23, 42, 0.08);
                    --glow: 0 0 0 1px rgba(15, 23, 42, 0.04), 0 8px 22px rgba(15, 23, 42, 0.06);
                    --space-1: 8px;
                    --space-2: 14px;
                    --space-3: 18px;
                    --space-4: 24px;
                }
                * { box-sizing: border-box; }
                body {
                    background:
                        radial-gradient(circle at 15% 20%, rgba(15, 111, 255, 0.12), transparent 35%),
                        radial-gradient(circle at 85% 10%, rgba(27, 185, 138, 0.10), transparent 40%),
                        linear-gradient(180deg, rgba(255, 255, 255, 0.9), rgba(244, 242, 239, 0.85)),
                        var(--bg);
                    font-family: 'Space Grotesk', 'Segoe UI', system-ui, -apple-system, sans-serif;
                    color: var(--text);
                    margin: 0;
                }
                .page { padding: 28px; max-width: 1200px; margin: 0 auto; }
                .hero {
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                    gap: 24px;
                    padding: 20px 24px;
                    background: var(--panel);
                    border-radius: 20px;
                    box-shadow: var(--shadow);
                    border: 1px solid var(--border);
                    margin-bottom: 18px;
                    backdrop-filter: blur(10px);
                }
                .hero-copy { display: flex; flex-direction: column; gap: 6px; }
                .eyebrow {
                    text-transform: uppercase;
                    letter-spacing: 0.24em;
                    font-size: 11px;
                    color: var(--muted);
                }
                .hero-title {
                    margin: 0;
                    font-size: 30px;
                    font-weight: 700;
                    letter-spacing: -0.02em;
                    font-family: 'Fraunces', 'Space Grotesk', serif;
                }
                .hero-subtitle { color: var(--muted); margin-top: 4px; max-width: 520px; }
                .hero-meta-wrap { display: flex; flex-direction: column; gap: 8px; align-items: flex-end; }
                .hero-meta { font-size: 14px; font-weight: 600; color: var(--accent-strong); }
                .tabs { margin-top: var(--space-2); }
                .tabs .tab { background: transparent; border: none; }
                .tabs .tab-selected {
                    background: var(--panel);
                    color: var(--text);
                    border-bottom: 2px solid var(--accent);
                    box-shadow: var(--glow);
                }
                .tab-content { display: flex; flex-direction: column; gap: var(--space-3); }
                .dashboard-stack { display: flex; flex-direction: column; gap: var(--space-3); }
                .stack { display: flex; flex-direction: column; gap: var(--space-2); }
                .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: var(--space-2); margin-bottom: 0; }
                .card {
                    background: var(--card);
                    padding: 16px 18px;
                    border-radius: 16px;
                    box-shadow: var(--shadow);
                    border: 1px solid var(--border);
                }
                .kpi-card { position: relative; overflow: hidden; }
                .kpi-card::after {
                    content: "";
                    position: absolute;
                    right: -20px;
                    top: -30px;
                    width: 90px;
                    height: 90px;
                    background: radial-gradient(circle, rgba(15, 111, 255, 0.14), transparent 70%);
                }
                .kpi-label {
                    color: var(--muted);
                    font-size: 11px;
                    text-transform: uppercase;
                    letter-spacing: 0.12em;
                }
                .kpi-value { font-size: 26px; font-weight: 700; margin-top: 6px; }
                .kpi-delta { color: #149f76; font-size: 12px; margin-top: 4px; }
                .shadow { box-shadow: var(--shadow); }
                .row { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: var(--space-2); }
                .panel-graph {
                    background: var(--card);
                    border-radius: 16px;
                    padding: 6px;
                    box-shadow: var(--shadow);
                    border: 1px solid var(--border);
                }
                .graph-card { width: 100%; height: 100%; }
                .section-title { margin: 0; font-size: 22px; }
                .section-eyebrow {
                    text-transform: uppercase;
                    font-size: 11px;
                    letter-spacing: 0.12em;
                    color: var(--muted);
                }
                .section-subtitle { color: var(--muted); margin-top: 6px; }
                .section-head { display: flex; flex-direction: column; gap: 6px; margin-bottom: 4px; }
                .section-block { display: flex; flex-direction: column; gap: var(--space-2); }
                .subtitle { color: var(--muted); margin: 6px 0 12px 0; }
                .panel {
                    padding: var(--space-3);
                    background: var(--panel);
                    border-radius: 18px;
                    box-shadow: var(--shadow);
                    border: 1px solid var(--border);
                }
                .textarea {
                    width: 100%;
                    min-height: 120px;
                    border-radius: 12px;
                    border: 1px solid var(--border);
                    padding: 12px;
                    background: #fefefe;
                    color: var(--text);
                    font-size: 14px;
                }
                .primary-btn {
                    background: linear-gradient(135deg, var(--accent), var(--accent2));
                    color: white;
                    border: none;
                    padding: 10px 18px;
                    border-radius: 12px;
                    cursor: pointer;
                    font-weight: 600;
                    box-shadow: 0 12px 24px rgba(15, 111, 255, 0.18);
                }
                .primary-btn:hover { opacity: 0.95; }
                .pill-label {
                    display: inline-flex;
                    align-items: center;
                    padding: 4px 10px;
                    border-radius: 999px;
                    background: rgba(15, 111, 255, 0.08);
                    color: var(--muted);
                    font-size: 11px;
                    text-transform: uppercase;
                    letter-spacing: 0.12em;
                }
                .answer-card { margin-top: 6px; color: var(--text); }
                .code-block {
                    background: #f3f5fb;
                    color: #30394f;
                    padding: 12px;
                    border-radius: 12px;
                    white-space: pre-wrap;
                    border: 1px solid var(--border);
                }
                .subtabs { margin-top: var(--space-2); }
                .subtabs .tab { background: transparent; }
                .subtabs .tab-selected {
                    background: var(--panel);
                    color: var(--text);
                    border-bottom: 2px solid var(--accent2);
                    box-shadow: var(--glow);
                }
                .calendar-wrap { display: flex; flex-direction: column; gap: var(--space-1); }
                .calendar-title { font-weight: 600; }
                .calendar-grid { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); border: 1px solid var(--border); border-top: none; }
                .cal-head-row { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); }
                .cal-head { text-align: left; font-size: 12px; color: var(--muted); padding: 8px 10px; border-bottom: 1px solid var(--border); }
                .cal-cell {
                    border-top: 1px solid var(--border);
                    border-right: 1px solid var(--border);
                    padding: 8px 10px;
                    min-height: 110px;
                    background: #ffffff;
                }
                .cal-cell:nth-child(7n) { border-right: none; }
                .cal-empty { background: #f1f3f7; }
                .cal-day { font-weight: 600; font-size: 12px; color: var(--text); margin-bottom: 6px; }
                .cal-events { display: flex; flex-direction: column; gap: 4px; }
                .cal-chip-btn {
                    background: rgba(15, 111, 255, 0.12);
                    color: #1f2a4b;
                    padding: 4px 6px;
                    border-radius: 8px;
                    font-size: 11px;
                    text-align: left;
                    white-space: nowrap;
                    overflow: hidden;
                    text-overflow: ellipsis;
                    border: none;
                    cursor: pointer;
                }
                .cal-chip-btn:hover { background: rgba(15, 111, 255, 0.2); }
                .cal-more { font-size: 11px; color: var(--muted); }
                .calendar-empty { color: var(--muted); }
                .calendar-toolbar { display: flex; align-items: center; gap: var(--space-2); margin-bottom: var(--space-2); flex-wrap: wrap; }
                .calendar-time { min-width: 180px; }
                .day-list { display: flex; flex-direction: column; gap: var(--space-2); }
                .day-item-btn {
                    display: grid;
                    grid-template-columns: 90px 1fr;
                    gap: 12px;
                    padding: 10px 12px;
                    border-radius: 12px;
                    border: 1px solid var(--border);
                    background: #ffffff;
                    text-align: left;
                    cursor: pointer;
                }
                .day-item-btn:hover { border-color: rgba(15, 111, 255, 0.4); background: rgba(15, 111, 255, 0.04); }
                .day-time { font-weight: 600; color: var(--text); }
                .day-details { display: flex; flex-direction: column; gap: 2px; }
                .day-title { font-weight: 600; }
                .day-subtitle { font-size: 12px; color: var(--muted); }
                .calendar-detail {
                    margin-top: var(--space-2);
                    padding: 12px;
                    border-radius: 12px;
                    border: 1px solid var(--border);
                    background: #ffffff;
                }
                h2, h3 { color: var(--text); }

                /* Date picker theming */
                .DateRangePickerInput {
                    background: #ffffff;
                    border: 1px solid var(--border);
                    border-radius: 12px;
                    box-shadow: var(--glow);
                    padding: 4px 6px;
                }
                .DateInput {
                    background: transparent;
                }
                .DateInput_input {
                    font-family: 'Space Grotesk', 'Segoe UI', system-ui, -apple-system, sans-serif;
                    font-size: 13px;
                    color: var(--text);
                    background: transparent;
                    border: none;
                    padding: 6px 8px;
                }
                .DateRangePickerInput_arrow {
                    color: var(--muted);
                }
                .DateInput_input__focused {
                    border: none;
                    outline: none;
                    background: rgba(15, 111, 255, 0.08);
                    border-radius: 8px;
                }
                .CalendarDay__default {
                    border: 1px solid transparent;
                    color: var(--text);
                }
                .CalendarDay__default:hover {
                    background: rgba(15, 111, 255, 0.08);
                    border: 1px solid rgba(15, 111, 255, 0.2);
                }
                .CalendarDay__selected, .CalendarDay__selected:active, .CalendarDay__selected:hover {
                    background: var(--accent);
                    border: 1px solid var(--accent);
                    color: #ffffff;
                }
                .CalendarDay__selected_span, .CalendarDay__selected_span:hover {
                    background: rgba(15, 111, 255, 0.18);
                    border: 1px solid rgba(15, 111, 255, 0.18);
                    color: var(--text);
                }
                .CalendarDay__hovered_span, .CalendarDay__hovered_span:hover {
                    background: rgba(15, 111, 255, 0.12);
                    border: 1px solid rgba(15, 111, 255, 0.12);
                    color: var(--text);
                }
                .DayPickerKeyboardShortcuts_show__bottomRight {
                    border-right: 33px solid rgba(15, 111, 255, 0.3);
                }

                /* DatePickerSingle aligns with DateRangePicker */
                .SingleDatePickerInput {
                    background: #ffffff;
                    border: 1px solid var(--border);
                    border-radius: 12px;
                    box-shadow: var(--glow);
                    padding: 4px 6px;
                }
                .SingleDatePickerInput__withBorder { border: 1px solid var(--border); }

                /* Dropdown theming (react-select) */
                .Select-control {
                    background: #ffffff;
                    border: 1px solid var(--border);
                    border-radius: 12px;
                    box-shadow: var(--glow);
                    min-height: 36px;
                }
                .Select-placeholder,
                .Select--single > .Select-control .Select-value {
                    color: var(--text);
                    line-height: 36px;
                }
                .Select-input > input {
                    color: var(--text);
                    font-family: 'Space Grotesk', 'Segoe UI', system-ui, -apple-system, sans-serif;
                }
                .Select-menu-outer {
                    border: 1px solid var(--border);
                    border-radius: 12px;
                    box-shadow: var(--shadow);
                }
                .Select-option {
                    background: #ffffff;
                    color: var(--text);
                }
                .Select-option.is-focused {
                    background: rgba(15, 111, 255, 0.08);
                    color: var(--text);
                }
                .Select-option.is-selected {
                    background: rgba(15, 111, 255, 0.18);
                    color: var(--text);
                }
                .Select-arrow { border-color: var(--muted) transparent transparent; }
                .is-open > .Select-control .Select-arrow { border-color: transparent transparent var(--muted); }

                @media (max-width: 900px) {
                    .hero { flex-direction: column; align-items: flex-start; }
                    .hero-meta-wrap { align-items: flex-start; }
                }
                @media (max-width: 600px) {
                    .page { padding: 18px; }
                    .cards { grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); }
                    .row { grid-template-columns: 1fr; }
                }
            </style>
        </head>
        <body>
            {%app_entry%}
            <footer>
                {%config%}
                {%scripts%}
                {%renderer%}
            </footer>
        </body>
    </html>
    """
    return app


def main():
    eng = get_engine()
    agent = AnalyticsAgent()
    try:
        appts, calls, hours, kpis = load_data(eng)
    except OperationalError as e:
        print(f"Database connection failed: {e}")
        print("Check that MySQL is reachable with your DB_* env vars.")
        return
    metrics = compute_metrics(appts, calls, hours, kpis)
    figs = build_figures(metrics)
    app = build_app(metrics, figs)

    # Callback: date range refresh
    @app.callback(
        Output("dashboard-panel", "children"),
        [Input("date-range", "start_date"), Input("date-range", "end_date")]
    )
    def update_dashboard(start_date, end_date):
        try:
            start = pd.to_datetime(start_date).date() if start_date else None
            end = pd.to_datetime(end_date).date() if end_date else None
            appts_f, calls_f, hours_f, kpis_f = load_data(eng, start, end)
            metrics_f = compute_metrics(appts_f, calls_f, hours_f, kpis_f)
            figs_f = build_figures(metrics_f)
            return serve_layout(metrics_f, figs_f)
        except Exception as e:
            return html.Div(f"Error loading data: {e}")

    # Callback: calendar day view refresh
    @app.callback(
        Output("calendar-day-view", "children"),
        [Input("calendar-day", "date"), Input("calendar-time", "value"), Input("calendar-data", "data")]
    )
    def update_calendar_day(selected_date, selected_time, data):
        if not data:
            return html.Div("No appointments available.", className="calendar-empty")
        appts_df = pd.DataFrame(data)
        return build_day_view(appts_df, selected_date, selected_time)

    @app.callback(
        Output("calendar-detail", "children"),
        [Input({"type": "cal-event", "index": ALL}, "n_clicks")],
        [State("calendar-data", "data")]
    )
    def update_calendar_detail(n_clicks, data):
        if not data:
            return "Select an event to see details."
        ctx = callback_context
        if not ctx.triggered:
            return "Select an event to see details."
        trigger = ctx.triggered[0]["prop_id"].split(".")[0]
        if not trigger:
            return "Select an event to see details."
        try:
            event_id = json.loads(trigger)["index"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return "Select an event to see details."
        match = next((row for row in data if row.get("event_id") == event_id), None)
        if not match:
            return "Select an event to see details."
        service_name = match.get("service_name") or "Service"
        if service_name == "Unknown":
            service_name = "Service"
        staff_name = match.get("staff_name") or "Unassigned"
        date_label = match.get("appointment_date")
        time_label = match.get("appointment_time")
        if date_label and pd.notna(date_label):
            date_label = pd.to_datetime(date_label, errors="coerce").strftime("%b %d, %Y")
        else:
            date_label = "TBD"
        if time_label and pd.notna(time_label):
            time_label = pd.to_datetime(time_label, errors="coerce").strftime("%I:%M %p").lstrip("0")
        else:
            time_label = "TBD"
        return html.Div([
            html.Div(service_name, className="day-title"),
            html.Div(f"{date_label} • {time_label}", className="day-subtitle"),
            html.Div(f"Staff: {staff_name}", className="day-subtitle"),
            html.Div(f"Status: {match.get('status', 'scheduled')}", className="day-subtitle"),
            html.Div(f"Duration: {match.get('duration_minutes', '30')} min", className="day-subtitle"),
        ])

    # Callback for conversational analytics
    @app.callback(
        [Output("qa-answer", "children"), Output("qa-sql", "children")],
        [Input("qa-submit", "n_clicks")],
        [State("qa-input", "value")]
    )
    def run_analytics(n_clicks, question):
        if not n_clicks or not question:
            return "", ""
        plan = agent.generate_sql(question)
        if plan.get("error"):
            return f"SQL generation error: {plan['error']}", ""
        sql = plan.get("sql", "")
        try:
            rows = safe_execute(eng, sql)
        except Exception as e:
            return f"SQL execution error: {e}", sql
        meta = {"row_count": len(rows)}
        summary = agent.summarize(question, rows, meta)
        return summary, sql

    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", 8050)))


if __name__ == "__main__":
    main()
