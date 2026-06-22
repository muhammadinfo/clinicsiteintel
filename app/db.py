"""SQLite persistence for saved address reports — the desktop equivalent
of the HTML dashboard's localStorage, but durable across app updates and
queryable for the multi-address comparison view."""
import json
import sqlite3
import time

from config import DB_PATH, ensure_app_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    address TEXT NOT NULL,
    created_at REAL NOT NULL,
    lvi_mean REAL,
    lvi_p05 REAL,
    lvi_p95 REAL,
    report_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pasted_listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER,
    raw_text TEXT,
    parsed_json TEXT,
    created_at REAL NOT NULL,
    FOREIGN KEY(report_id) REFERENCES reports(id)
);
"""


def get_conn() -> sqlite3.Connection:
    ensure_app_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    return conn


def save_report(address: str, report_dict: dict) -> int:
    conn = get_conn()
    lvi = report_dict.get("lvi_summary", {})
    cur = conn.execute(
        "INSERT INTO reports (address, created_at, lvi_mean, lvi_p05, lvi_p95, report_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (address, time.time(), lvi.get("mean"), lvi.get("p05"), lvi.get("p95"), json.dumps(report_dict)),
    )
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def list_reports() -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, address, created_at, lvi_mean, lvi_p05, lvi_p95 FROM reports ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [
        {"id": r[0], "address": r[1], "created_at": r[2], "lvi_mean": r[3], "lvi_p05": r[4], "lvi_p95": r[5]}
        for r in rows
    ]


def get_report(report_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT report_json FROM reports WHERE id=?", (report_id,)).fetchone()
    conn.close()
    return json.loads(row[0]) if row else None


def delete_report(report_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM reports WHERE id=?", (report_id,))
    conn.execute("DELETE FROM pasted_listings WHERE report_id=?", (report_id,))
    conn.commit()
    conn.close()


def save_pasted_listing(report_id: int | None, raw_text: str, parsed: dict):
    conn = get_conn()
    conn.execute(
        "INSERT INTO pasted_listings (report_id, raw_text, parsed_json, created_at) VALUES (?, ?, ?, ?)",
        (report_id, raw_text, json.dumps(parsed), time.time()),
    )
    conn.commit()
    conn.close()
