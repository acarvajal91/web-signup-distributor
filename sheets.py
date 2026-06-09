"""
Google Sheets backend with automatic retries and safe empty-DataFrame handling.
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


@st.cache_resource(ttl=600)
def get_client() -> gspread.Client:
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    for attempt in range(3):
        try:
            return gspread.authorize(creds)
        except Exception:
            if attempt < 2:
                time.sleep(2)
    return gspread.authorize(creds)


def get_sheet(spreadsheet_id: str, tab_name: str) -> gspread.Worksheet:
    for attempt in range(3):
        try:
            gc = get_client()
            sh = gc.open_by_key(spreadsheet_id)
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
                return ws
        except gspread.exceptions.APIError:
            if attempt < 2:
                st.cache_resource.clear()
                time.sleep(3)
    raise Exception("No se pudo conectar al Sheet después de 3 intentos.")


def _safe_df(data: list, expected_cols: list) -> pd.DataFrame:
    """Returns a DataFrame guaranteed to have expected_cols, even if data is empty."""
    if not data:
        return pd.DataFrame(columns=expected_cols)
    df = pd.DataFrame(data)
    for col in expected_cols:
        if col not in df.columns:
            df[col] = ""
    return df


# ── config ────────────────────────────────────────────────────

def _get_config(spreadsheet_id: str) -> dict[str, str]:
    ws = get_sheet(spreadsheet_id, "config")
    records = ws.get_all_records()
    return {r["key"]: r["value"] for r in records if r.get("key")}


def _set_config(spreadsheet_id: str, key: str, value: str):
    ws = get_sheet(spreadsheet_id, "config")
    all_vals = ws.get_all_values()
    if not all_vals:
        ws.append_row([key, value])
        return
    for i, row in enumerate(all_vals[1:], start=2):
        if row and row[0] == key:
            ws.update_cell(i, 2, value)
            return
    ws.append_row([key, value])


def load_reps(spreadsheet_id: str) -> list[str]:
    cfg = _get_config(spreadsheet_id)
    raw = cfg.get("reps", "")
    return [r.strip() for r in raw.split("||") if r.strip()] if raw else []


def save_reps(spreadsheet_id: str, reps: list[str]):
    _set_config(spreadsheet_id, "reps", "||".join(reps))


def load_excluded_cats(spreadsheet_id: str) -> list[str]:
    cfg = _get_config(spreadsheet_id)
    raw = cfg.get("excluded_cats", "")
    return [c.strip() for c in raw.split("||") if c.strip()] if raw else []


def save_excluded_cats(spreadsheet_id: str, cats: list[str]):
    _set_config(spreadsheet_id, "excluded_cats", "||".join(cats))


# ── assignments ───────────────────────────────────────────────

def load_assignments(spreadsheet_id: str, month: Optional[str] = None) -> pd.DataFrame:
    ws = get_sheet(spreadsheet_id, "assignments")
    df = _safe_df(ws.get_all_records(), ASSIGNMENTS_COLS)
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


# ── days worked ───────────────────────────────────────────────

def load_days_worked(spreadsheet_id: str, month: str) -> dict[str, int]:
    ws = get_sheet(spreadsheet_id, "days_worked")
    df = _safe_df(ws.get_all_records(), DAYS_COLS)
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
        return
    header = all_vals[0]
    try:
        month_col = header.index("month") + 1
        rep_col = header.index("rep") + 1
        days_col = header.index("days_worked") + 1
    except ValueError:
        ws.append_row([month, rep, days])
        return
    for i, row in enumerate(all_vals[1:], start=2):
        if len(row) >= max(month_col, rep_col) and row[month_col-1] == month and row[rep_col-1] == rep:
            ws.update_cell(i, days_col, days)
            return
    ws.append_row([month, rep, days])


def decrement_day(spreadsheet_id: str, month: str, rep: str):
    current = load_days_worked(spreadsheet_id, month).get(rep, 0)
    if current > 0:
        upsert_days_worked(spreadsheet_id, month, rep, current - 1)


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
