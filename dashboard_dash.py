"""Dash-based dashboard for Voice Facilitator data.

Usage:
    pip install dash pandas plotly sqlalchemy pymysql python-dotenv
    python dashboard_dash.py

The app will listen on http://127.0.0.1:8050 by default.
"""
import os
from datetime import date, datetime, timedelta
from typing import Optional

# Stub bottleneck if compiled wheel conflicts with NumPy >=2
import types
import sys
# Stub bottleneck with a compatible version string to silence pandas optional dependency warning
sys.modules.setdefault("bottleneck", types.SimpleNamespace(__name__="bottleneck", __version__="1.3.6"))

# Avoid loading bottleneck compiled against incompatible NumPy
os.environ.setdefault("PANDAS_NO_BOTTLENECK", "1")

import pandas as pd
import plotly.graph_objects as go
from dash import Dash, dcc, html
from dash.dependencies import Input, Output, State
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
    appts["appointment_time"] = pd.to_datetime(appts["appointment_time"], errors="coerce")
    appts["created_at"] = pd.to_datetime(appts["created_at"], errors="coerce")
    kpis["created_at"] = pd.to_datetime(kpis.get("created_at"))
    if "appointment_date" in kpis.columns:
        kpis["appointment_date"] = pd.to_datetime(kpis["appointment_date"], errors="coerce")
    if "appointment_time" in kpis.columns:
        kpis["appointment_time"] = pd.to_datetime(kpis["appointment_time"], errors="coerce")

    bookings_7 = window_count(last_7, today, appts, status="scheduled")
    bookings_prev = window_count(prev_7_start, prev_7_end, appts, status="scheduled")

    conversion = None
    if not calls.empty:
        calls_booked = calls[calls["outcome"] == "booked"].shape[0]
        conversion = (calls_booked / max(len(calls), 1)) * 100

    appts["service_price"] = pd.to_numeric(appts["service_price"], errors="coerce").fillna(0)
    appts["service_name"] = appts["service_name"].fillna("Unknown")
    appts["staff_name"] = appts["staff_name"].fillna("Unassigned")

    # Revenue from kpi_events (booked) to avoid null service_ids
    revenue_30 = 0
    if not kpis.empty:
        kpi_booked = kpis[kpis["event_type"] == "booked"].copy()
        kpi_booked["service_price"] = pd.to_numeric(kpi_booked["service_price"], errors="coerce").fillna(0)
        revenue_30 = kpi_booked[
            kpi_booked["created_at"].dt.date >= today - timedelta(days=29)
        ]["service_price"].sum()

    total_recent = appts[appts["appointment_date"].dt.date >= today - timedelta(days=29)]
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
        "revenue_30": revenue_30,
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


def serve_layout(metrics, figs, start_date=None, end_date=None):
    bookings_delta = ((metrics["bookings_7"] - metrics["bookings_prev"]) / max(metrics["bookings_prev"], 1)) * 100
    cards = [
        {"label": "Bookings (7d)", "value": metrics["bookings_7"], "delta": f"{bookings_delta:.1f}% vs prev 7d"},
        {"label": "Conversion", "value": f"{metrics['conversion']:.1f}%" if metrics["conversion"] is not None else "-", "delta": ""},
        {"label": "Revenue (30d)", "value": f"${metrics['revenue_30']:.0f}", "delta": ""},
        {"label": "No-show rate (30d)", "value": f"{metrics['no_show_rate']:.1f}%", "delta": ""},
    ]

    card_divs = [
        html.Div([
            html.Div(card["label"], style={"color": "#6c757d", "fontSize": "12px", "textTransform": "uppercase", "letterSpacing": "0.05em"}),
            html.Div(card["value"], style={"fontSize": "24px", "fontWeight": "600"}),
            html.Div(card["delta"], style={"color": "#198754", "fontSize": "12px"}),
        ], className="card") for card in cards
    ]

    return html.Div([
        html.H2("Business Dashboard"),
        html.Div(card_divs, className="cards"),
        dcc.Graph(figure=figs[0]),
        dcc.Graph(figure=figs[1]),
        html.Div([
            html.Div(dcc.Graph(figure=figs[2]), className="half"),
            html.Div(dcc.Graph(figure=figs[3]), className="half"),
        ], className="row"),
        html.Div([
            html.Div(dcc.Graph(figure=figs[4]), className="half"),
            html.Div(dcc.Graph(figure=figs[5]), className="half"),
        ], className="row"),
    ], style={"padding": "16px", "display": "flex", "flexDirection": "column", "gap": "16px"})


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
        html.Div(id="dashboard-panel", children=serve_layout(metrics, figs))
    ])
    qa_tab = html.Div([
        html.H3("Conversational Analytics", className="section-title"),
        html.Div("Ask a question about your business. We’ll translate to safe SQL and summarize.", className="subtitle"),
        dcc.Textarea(id="qa-input", placeholder="e.g., How many bookings last week? Revenue by staff this month?", className="textarea"),
        html.Button("Ask", id="qa-submit", n_clicks=0, className="primary-btn"),
        html.Div([
            html.Div("Answer", className="pill-label"),
            html.Div(id="qa-answer", className="answer-card")
        ], className="card shadow"),
        html.Div([
            html.Div("SQL", className="pill-label"),
            html.Pre(id="qa-sql", className="code-block")
        ], className="card shadow")
    ], className="panel")

    app.layout = html.Div([
        html.Div([
            html.H1("Voice Facilitator Insights", className="hero-title"),
            html.Div("Operational KPIs and conversational analytics in one place.", className="hero-subtitle")
        ], className="hero"),
        dcc.Tabs([
            dcc.Tab(label="Dashboard", children=[graph_tab], className="tab", selected_className="tab-selected"),
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
                :root {
                    --bg: #f7f9fc;
                    --panel: #ffffff;
                    --card: #ffffff;
                    --accent: #5c6cff;
                    --accent2: #23c197;
                    --text: #1f2430;
                    --muted: #7b8295;
                    --shadow: 0 10px 30px rgba(15, 23, 42, 0.08);
                    --border: #e8ecf5;
                }
                * { box-sizing: border-box; }
                body { background: radial-gradient(circle at 20% 20%, rgba(92,108,255,0.08), transparent 25%), radial-gradient(circle at 80% 0%, rgba(35,193,151,0.08), transparent 30%), var(--bg); font-family: 'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif; color: var(--text); margin: 0; }
                .page { padding: 20px; }
                .hero { margin-bottom: 16px; }
                .hero-title { margin: 0; font-size: 28px; font-weight: 700; letter-spacing: -0.02em; }
                .hero-subtitle { color: var(--muted); margin-top: 4px; }
                .tabs .tab { background: transparent; }
                .tabs .tab-selected { background: var(--panel); color: var(--text); border-bottom: 2px solid var(--accent); }
                .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 16px; }
                .card { background: var(--card); padding: 14px 16px; border-radius: 14px; box-shadow: var(--shadow); border: 1px solid var(--border); }
                .shadow { box-shadow: var(--shadow); }
                .row { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 16px; }
                .half { background: var(--card); padding: 8px; border-radius: 14px; box-shadow: var(--shadow); border: 1px solid var(--border); }
                .dash-graph { background: var(--card); padding: 8px; border-radius: 12px; box-shadow: var(--shadow); }
                .section-title { margin: 0; font-size: 22px; }
                .subtitle { color: var(--muted); margin: 6px 0 12px 0; }
                .panel { padding: 16px; background: var(--panel); border-radius: 16px; box-shadow: var(--shadow); border: 1px solid var(--border); }
                .textarea { width: 100%; min-height: 120px; border-radius: 12px; border: 1px solid var(--border); padding: 10px; background: #fdfefe; color: var(--text); }
                .primary-btn { background: linear-gradient(135deg, var(--accent), var(--accent2)); color: white; border: none; padding: 10px 18px; border-radius: 10px; cursor: pointer; font-weight: 600; }
                .primary-btn:hover { opacity: 0.95; }
                .pill-label { display: inline-block; padding: 4px 10px; border-radius: 20px; background: #eef1fb; color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; }
                .answer-card { margin-top: 6px; color: var(--text); }
                .code-block { background: #f4f6fb; color: #30394f; padding: 10px; border-radius: 10px; white-space: pre-wrap; border: 1px solid var(--border); }
                h2, h3 { color: var(--text); }
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
