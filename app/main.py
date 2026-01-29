from datetime import datetime, timezone
from fastapi import FastAPI
from pydantic import BaseModel, Field
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

from db import init_db, get_conn

app = FastAPI(title="Job Prep Tracker")

# --- Prometheus Metrics ---
prep_minutes = Gauge("prep_minutes", "Minutes spent on prep (latest entry)", ["type"])
sleep_hours_g = Gauge("sleep_hours", "Sleep hours (latest entry)")
readiness_score_g = Gauge("readiness_score", "Simple readiness score (latest entry)")

leetcode_solved_total = Counter("leetcode_solved_total", "Total LeetCode solved")

corr_sleep_vs_leetcode = Gauge(
    "corr_sleep_vs_leetcode",
    "Pearson correlation between sleep_hours and leetcode_solved over recent logs"
)

ma_leetcode_solved = Gauge(
    "ma_leetcode_solved",
    "Moving average of leetcode_solved over recent logs"
)

ma_total_prep_minutes = Gauge(
    "ma_total_prep_minutes",
    "Moving average of total prep minutes (dsa+ml+sql+cloud) over recent logs"
)

forecast_next_leetcode = Gauge(
    "forecast_next_leetcode",
    "Naive forecast for next leetcode_solved (uses moving average)"
)


class DailyLog(BaseModel):
    dsa_minutes: int = Field(ge=0, le=1440)
    ml_minutes: int = Field(ge=0, le=1440)
    sql_minutes: int = Field(ge=0, le=1440)
    cloud_minutes: int = Field(ge=0, le=1440)
    leetcode_solved: int = Field(ge=0, le=200)
    sleep_hours: float = Field(ge=0, le=24)


@app.on_event("startup")
def on_startup():
    init_db()


def compute_readiness(log: DailyLog) -> float:
    total_minutes = log.dsa_minutes + log.ml_minutes + log.sql_minutes + log.cloud_minutes
    score = (0.02 * total_minutes) + (2.0 * log.leetcode_solved)

    if log.sleep_hours < 6:
        score -= 3.0
    elif log.sleep_hours >= 7:
        score += 1.0

    return max(score, 0.0)


def pearson_corr(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den_x = sum((x - mean_x) ** 2 for x in xs)
    den_y = sum((y - mean_y) ** 2 for y in ys)
    den = (den_x * den_y) ** 0.5
    return (num / den) if den != 0 else 0.0


def moving_average(vals: list[float]) -> float:
    return (sum(vals) / len(vals)) if vals else 0.0


def recompute_insights(window: int = 50) -> None:
    """
    Reads recent logs from SQLite, computes correlation + moving averages,
    and updates Prometheus gauges.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT sleep_hours, leetcode_solved,
               (dsa_minutes + ml_minutes + sql_minutes + cloud_minutes) AS total_prep
        FROM daily_log
        ORDER BY id DESC
        LIMIT ?
        """,
        (window,),
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return

    rows = list(reversed(rows))

    sleeps = [float(r["sleep_hours"]) for r in rows]
    leets = [float(r["leetcode_solved"]) for r in rows]
    totals = [float(r["total_prep"]) for r in rows]

    r = pearson_corr(sleeps, leets)
    ma_leet = moving_average(leets)
    ma_total = moving_average(totals)

    corr_sleep_vs_leetcode.set(r)
    ma_leetcode_solved.set(ma_leet)
    ma_total_prep_minutes.set(ma_total)

    # Naive forecast: next value ~= moving average
    forecast_next_leetcode.set(ma_leet)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/log")
def add_log(log: DailyLog):
    ts = datetime.now(timezone.utc).isoformat()

    # Store in SQLite
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO daily_log(ts, dsa_minutes, ml_minutes, sql_minutes, cloud_minutes,
                              leetcode_solved, sleep_hours)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (ts, log.dsa_minutes, log.ml_minutes, log.sql_minutes, log.cloud_minutes,
         log.leetcode_solved, log.sleep_hours),
    )
    conn.commit()
    conn.close()

    # Update Prometheus metrics
    prep_minutes.labels(type="dsa").set(log.dsa_minutes)
    prep_minutes.labels(type="ml").set(log.ml_minutes)
    prep_minutes.labels(type="sql").set(log.sql_minutes)
    prep_minutes.labels(type="cloud").set(log.cloud_minutes)
    sleep_hours_g.set(log.sleep_hours)

    score = compute_readiness(log)
    readiness_score_g.set(score)

    # Increment counter by today's solved
    leetcode_solved_total.inc(log.leetcode_solved)

    # ✅ NEW: recompute DS insight metrics from recent logs
    recompute_insights(window=50)

    return {"message": "logged", "readiness_score": score, "ts": ts}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
