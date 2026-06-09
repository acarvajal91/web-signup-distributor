"""
Google Sheets backend — aggressive caching to avoid 429 rate limits.
Reads are cached for 60s. Writes invalidate the cache immediately.
"""

import time
from typing import Optional

import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

ASSIGNMENTS_COLS = [
    "date", "month", "assigned_rep", "vendor_id", "vendor_name",
    "category", "region", "phone", "mail",
]
DAYS_COLS = ["month", "rep", "days_worked"]
CONFIG_COLS = ["key", "value"]


# ── auth ──────────────────────────────────────────────────────

@st.cache_resource(ttl=3600)
def get_client() -> gspread.Client:
    import google.auth.transport.requests
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    creds.refresh(google.auth.transport.requests.Request())
    return gspread.Client(auth=creds)


def _open_sheet(spreadsheet_id: str) -> gspread.Spreadsheet:
    for attempt in range(3):
        try:
            return get_client().open_by_key(spreadsheet_id)
        except Exception:
            if attempt < 2:
                st.cache_resource.clear()
                time.sleep(4)
    raise Exception("No se pudo conectar al Sheet después de 3 intentos.")


def get_sheet(spreadsheet_id: str, tab_name: str) -> gspread.Worksheet:
    sh = _open_sheet(spreadsheet_id)
    try:
        return sh.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=tab_name, rows=1000, cols=20)
        headers = {
            "assignments": ASSIGNMENTS_COLS,
            "days_worked": DAYS_COLS,
            "config": CONFIG_COLS,
        }
        if tab_name in headers:
            ws.append_row(headers[tab_name])
        time.sleep(1)
        return ws


# ── cached reads (60s TTL) ────────────────────────────────────
# Each read function is cached independently.
# Call st.cache_data.clear() after any write to invalidate.

@st.cache_data(ttl=60)
def _read_tab(spreadsheet_id: str, tab_name: str) -> list[dict]:
    """Single cached read per tab. All logic built on top of this."""
    ws = get_sheet(spreadsheet_id, tab_name)
    return ws.get_all_records()


def _safe_df(data: list, expected_cols: list) -> pd.DataFrame:
    if not data:
        return pd.DataFrame(columns=expected_cols)
    df = pd.DataFrame(data)
    for col in expected_cols:
        if col not in df.columns:
            df[col] = ""
    return df


# ── config ────────────────────────────────────────────────────

def load_reps(spreadsheet_id: str) -> list[str]:
    data = _read_tab(spreadsheet_id, "config")
    cfg = {r["key"]: r["value"] for r in data if r.get("key")}
    raw = cfg.get("reps", "")
    return [r.strip() for r in raw.split("||") if r.strip()] if raw else []


def save_reps(spreadsheet_id: str, reps: list[str]):
    _write_config(spreadsheet_id, "reps", "||".join(reps))


def load_excluded_cats(spreadsheet_id: str) -> list[str]:
    data = _read_tab(spreadsheet_id, "config")
    cfg = {r["key"]: r["value"] for r in data if r.get("key")}
    raw = cfg.get("excluded_cats", "")
    return [c.strip() for c in raw.split("||") if c.strip()] if raw else []


def save_excluded_cats(spreadsheet_id: str, cats: list[str]):
    _write_config(spreadsheet_id, "excluded_cats", "||".join(cats))


def _write_config(spreadsheet_id: str, key: str, value: str):
    ws = get_sheet(spreadsheet_id, "config")
    all_vals = ws.get_all_values()
    if not all_vals:
        ws.append_row([key, value])
    else:
        for i, row in enumerate(all_vals[1:], start=2):
            if row and row[0] == key:
                ws.update_cell(i, 2, value)
                st.cache_data.clear()
                return
        ws.append_row([key, value])
    st.cache_data.clear()


# ── assignments ───────────────────────────────────────────────

def load_assignments(spreadsheet_id: str, month: Optional[str] = None) -> pd.DataFrame:
    data = _read_tab(spreadsheet_id, "assignments")
    df = _safe_df(data, ASSIGNMENTS_COLS)
    if month and not df.empty and "month" in df.columns:
        df = df[df["month"] == month]
    return df


def save_assignments(spreadsheet_id: str, rows: list[dict], date_str: str):
    ws = get_sheet(spreadsheet_id, "assignments")
    month = date_str[:7]
    to_write = [
        [date_str, month] + [str(r.get(c, "")) for c in ASSIGNMENTS_COLS[2:]]
        for r in rows
    ]
    if to_write:
        ws.append_rows(to_write, value_input_option="RAW")
    st.cache_data.clear()


def delete_date(spreadsheet_id: str, date_str: str):
    ws = get_sheet(spreadsheet_id, "assignments")
    all_vals = ws.get_all_values()
    if not all_vals or len(all_vals) < 2:
        return
    try:
        date_col = all_vals[0].index("date")
    except ValueError:
        return
    to_delete = [
        i + 2 for i, row in enumerate(all_vals[1:])
        if len(row) > date_col and row[date_col] == date_str
    ]
    for idx in reversed(to_delete):
        ws.delete_rows(idx)
    st.cache_data.clear()


# ── days worked ───────────────────────────────────────────────

def load_days_worked(spreadsheet_id: str, month: str) -> dict[str, int]:
    data = _read_tab(spreadsheet_id, "days_worked")
    df = _safe_df(data, DAYS_COLS)
    if df.empty or "month" not in df.columns:
        return {}
    month_df = df[df["month"] == month]
    if month_df.empty:
        return {}
    return dict(zip(month_df["rep"], month_df["days_worked"].astype(int)))


def upsert_days_worked(spreadsheet_id: str, month: str, rep: str, days: int):
    ws = get_sheet(spreadsheet_id, "days_worked")
    all_vals = ws.get_all_values()
    if not all_vals:
        ws.append_row([month, rep, days])
        st.cache_data.clear()
        return
    header = all_vals[0]
    try:
        month_col = header.index("month") + 1
        rep_col = header.index("rep") + 1
        days_col = header.index("days_worked") + 1
    except ValueError:
        ws.append_row([month, rep, days])
        st.cache_data.clear()
        return
    for i, row in enumerate(all_vals[1:], start=2):
        if len(row) >= max(month_col, rep_col) and row[month_col-1] == month and row[rep_col-1] == rep:
            ws.update_cell(i, days_col, days)
            st.cache_data.clear()
            return
    ws.append_row([month, rep, days])
    st.cache_data.clear()


def recalc_days_from_history(spreadsheet_id: str, month: str, all_reps: list[str]):
    """Recount days from assignment history. Call after overwriting a day."""
    # Read directly from sheet, bypassing cache (which may be stale after delete)
    ws = get_sheet(spreadsheet_id, "assignments")
    data = ws.get_all_records()
    df = _safe_df(data, ASSIGNMENTS_COLS)
    month_df = df[df["month"] == month] if not df.empty and "month" in df.columns else pd.DataFrame()
    for r in all_reps:
        if not month_df.empty and "assigned_rep" in month_df.columns:
            days = int(month_df[month_df["assigned_rep"] == r]["date"].nunique())
        else:
            days = 0
        upsert_days_worked(spreadsheet_id, month, r, days)


# ── helpers ───────────────────────────────────────────────────

def get_processed_dates(spreadsheet_id: str, month: str) -> list[str]:
    df = load_assignments(spreadsheet_id, month)
    if df.empty or "date" not in df.columns:
        return []
    return sorted(df["date"].unique().tolist())


def date_already_exists(spreadsheet_id: str, date_str: str) -> bool:
    df = load_assignments(spreadsheet_id)
    if df.empty or "date" not in df.columns:
        return False
    return date_str in df["date"].values
