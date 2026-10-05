"""
Turns an uploaded CSV/Excel file into a flat list of raw row dicts.

Deliberately dumb: every value is kept as text exactly as the file had it
(dtype=str end to end, including for Excel), so a cell like "ERR" or a
locale-specific date string survives untouched into the normalization step
in ingestion.py, which is where the actual validity decisions are made. This
module only answers "where are the Time/Temperature/Logger columns", not
"is this row valid".
"""

import hashlib
from datetime import datetime

import pandas as pd

from fridges.models import Logger

# "timestamp"/"datetime" are unambiguous combined date+time columns.
# "date" and "time" alone are ambiguous: paired together they're separate
# date-part/time-part columns to combine; "time" alone is treated as a
# combined column (matches the sample data, where "Time" already holds a
# full "2026-09-14 06:00" value).
COMBINED_TIMESTAMP_ALIASES = {"timestamp", "datetime"}
DATE_ALIASES = {"date"}
TIME_ALIASES = {"time"}
TEMPERATURE_HEADER_ALIASES = {"temp", "temperature", "value"}
LOGGER_HEADER_ALIASES = {"logger", "loggerid", "loggerno", "loggernumber"}

# Tried in order. Branch/Fridge text in the file is never read — a known
# Logger-ID column is authoritative; everything else about placement comes
# from the LoggerAssignment registry, not from the file.
TIMESTAMP_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
]


class FileParseError(Exception):
    """The file can't be read at all (wrong type, no Time/Temperature column)."""


def compute_file_hash(file_obj) -> str:
    file_obj.seek(0)
    digest = hashlib.sha256()
    for chunk in iter(lambda: file_obj.read(8192), b""):
        digest.update(chunk)
    file_obj.seek(0)
    return digest.hexdigest()


def _normalize_header(value) -> str:
    return "".join(ch for ch in str(value).strip().lower() if ch.isalnum())


def detect_columns(columns) -> dict:
    """
    Maps the file's actual header names to our canonical roles. The time
    side resolves to either a single "timestamp" column, or a "date" +
    "time" pair to be combined per-row in read_rows() — see the alias
    comment above for how "Date"/"Time" are disambiguated.
    """
    normalized = {_normalize_header(c): c for c in columns}
    mapping = {}

    for col in columns:
        key = _normalize_header(col)
        if key in TEMPERATURE_HEADER_ALIASES and "temperature" not in mapping:
            mapping["temperature"] = col
        elif key in LOGGER_HEADER_ALIASES and "logger" not in mapping:
            mapping["logger"] = col

    combined_col = next((normalized[k] for k in normalized if k in COMBINED_TIMESTAMP_ALIASES), None)
    date_col = next((normalized[k] for k in normalized if k in DATE_ALIASES), None)
    time_col = next((normalized[k] for k in normalized if k in TIME_ALIASES), None)

    if combined_col:
        mapping["timestamp"] = combined_col
    elif date_col and time_col:
        mapping["date"] = date_col
        mapping["time"] = time_col
    elif time_col:
        mapping["timestamp"] = time_col
    # A lone Date column with no Time/Timestamp is deliberately NOT mapped —
    # every supported format needs a time-of-day component, so this file
    # type is unsupported rather than silently losing the time of day.

    has_time_info = "timestamp" in mapping or ("date" in mapping and "time" in mapping)
    if not has_time_info or "temperature" not in mapping:
        raise FileParseError(
            "Could not find both a Time (or Date+Time) and a Temperature column in this file."
        )
    return mapping


def read_rows(file_obj, filename: str) -> list[dict]:
    """
    Returns a list of {"logger_code": str | None, "timestamp_raw": str,
    "temperature_raw": str} dicts, in file order (the ingestion step sorts
    by parsed timestamp — rows aren't guaranteed to arrive in time order).
    """
    file_obj.seek(0)
    read_kwargs = {"dtype": str, "keep_default_na": False}
    lower_name = filename.lower()

    # pandas raises its own exceptions (EmptyDataError for a 0-byte file,
    # BadZipFile/ValueError for a corrupt .xlsx, etc.) straight out of the
    # parser — none of those are safe to let escape to the view as an
    # unhandled 500, so every read failure is normalized to FileParseError.
    try:
        if lower_name.endswith((".xlsx", ".xls")):
            df = pd.read_excel(file_obj, **read_kwargs)
        elif lower_name.endswith(".csv"):
            df = pd.read_csv(file_obj, **read_kwargs)
        else:
            raise FileParseError(f"Unsupported file type: {filename}. Expected .csv or .xlsx.")
    except FileParseError:
        raise
    except Exception as exc:
        raise FileParseError(
            f"Could not read '{filename}' — the file is empty, corrupted, or not a valid CSV/Excel file."
        ) from exc

    columns = detect_columns(df.columns)

    rows = []
    for _, row in df.iterrows():
        logger_code = None
        if "logger" in columns:
            raw_logger = str(row[columns["logger"]]).strip()
            logger_code = raw_logger or None

        if "timestamp" in columns:
            timestamp_raw = str(row[columns["timestamp"]]).strip()
        else:
            date_part = str(row[columns["date"]]).strip()
            time_part = str(row[columns["time"]]).strip()
            timestamp_raw = f"{date_part} {time_part}".strip()

        rows.append(
            {
                "logger_code": logger_code,
                "timestamp_raw": timestamp_raw,
                "temperature_raw": str(row[columns["temperature"]]).strip(),
            }
        )
    return rows


def parse_timestamp(raw: str):
    """Returns a naive datetime, or None if unparseable in any agreed format."""
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def parse_temperature(raw: str):
    """Returns a float, or None for anything unparseable (e.g. "ERR")."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def to_celsius(value: float, unit: str) -> float:
    if unit == Logger.UNIT_FAHRENHEIT:
        # round() to avoid binary floating-point noise (e.g. 3.4999999999999982
        # instead of 3.5) propagating into storage and API responses.
        return round((value - 32) * 5.0 / 9.0, 2)
    return value
