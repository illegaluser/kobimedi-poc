from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


DEFAULT_BOOKINGS_PATH = Path(__file__).resolve().parent.parent / "data" / "bookings.json"


def _resolve_path(path: str | Path | None = None) -> Path:
    if path is None:
        return DEFAULT_BOOKINGS_PATH
    return Path(path)


def load_bookings(path: str | Path | None = None) -> list[dict]:
    bookings_path = _resolve_path(path)
    try:
        raw_text = bookings_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []

    if not raw_text.strip():
        return []

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return []

    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def save_bookings(bookings: list[dict], path: str | Path | None = None) -> None:
    bookings_path = _resolve_path(path)
    bookings_path.parent.mkdir(parents=True, exist_ok=True)

    with NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=str(bookings_path.parent)) as temp_file:
        json.dump(bookings, temp_file, ensure_ascii=False, indent=2)
        temp_path = Path(temp_file.name)

    temp_path.replace(bookings_path)


def _next_booking_id(bookings: list[dict]) -> str:
    max_index = 0
    for booking in bookings:
        booking_id = str(booking.get("id") or "")
        if "-" not in booking_id:
            continue
        suffix = booking_id.rsplit("-", 1)[-1]
        if suffix.isdigit():
            max_index = max(max_index, int(suffix))
    return f"booking-{max_index + 1:03d}"


def create_booking(record: dict[str, Any], path: str | Path | None = None) -> dict:
    bookings = load_bookings(path)
    now = datetime.now(timezone.utc).isoformat()

    booking = dict(record)
    booking.setdefault("id", _next_booking_id(bookings))
    booking.setdefault("status", "active")
    booking.setdefault("created_at", now)
    booking["updated_at"] = now

    bookings.append(booking)
    save_bookings(bookings, path)
    return booking


def _extract_booking_date_time(booking: dict) -> tuple[str | None, str | None]:
    booking_time = booking.get("booking_time")
    if booking_time:
        raw_value = str(booking_time)
        if raw_value.endswith("Z"):
            raw_value = f"{raw_value[:-1]}+00:00"
        try:
            dt = datetime.fromisoformat(raw_value)
        except ValueError:
            dt = None
        if dt is not None:
            return dt.date().isoformat(), dt.strftime("%H:%M")

    return booking.get("date"), booking.get("time")


def find_bookings(
    customer_name: str | None = None,
    filters: dict[str, Any] | None = None,
    path: str | Path | None = None,
) -> list[dict]:
    filters = filters or {}
    results: list[dict] = []

    for booking in load_bookings(path):
        if booking.get("status", "active") != "active":
            continue
        if customer_name and booking.get("customer_name") != customer_name:
            continue
        if filters.get("id") and booking.get("id") != filters["id"]:
            continue
        if filters.get("department") and booking.get("department") != filters["department"]:
            continue
        if filters.get("booking_time") and booking.get("booking_time") != filters["booking_time"]:
            continue

        booking_date, booking_time = _extract_booking_date_time(booking)
        if filters.get("date") and booking_date != filters["date"]:
            continue
        if filters.get("time") and booking_time != filters["time"]:
            continue

        results.append(dict(booking))

    return results
