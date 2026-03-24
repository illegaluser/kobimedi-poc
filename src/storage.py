from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


DEFAULT_BOOKINGS_PATH = Path(__file__).resolve().parent.parent / "data" / "bookings.json"
REQUIRED_BOOKING_FIELDS = (
    "id",
    "patient_name",
    "patient_contact",
    "is_proxy_booking",
    "booking_time",
    "department",
    "customer_type",
    "status",
)


class StorageError(Exception):
    """Base exception for persistent booking storage failures."""


class StorageReadError(StorageError):
    """Raised when bookings.json cannot be read safely."""


class StorageDecodeError(StorageReadError):
    """Raised when bookings.json is corrupted or not valid JSON."""


class StorageWriteError(StorageError):
    """Raised when bookings.json cannot be written atomically."""


class StorageConflictError(StorageError):
    """Raised when a fresh storage recheck blocks persistence."""


class StorageValidationError(StorageError):
    """Raised when a new booking record is missing required fields."""


def _resolve_path(path: str | Path | None = None) -> Path:
    if path is None:
        return DEFAULT_BOOKINGS_PATH
    return Path(path)


def normalize_patient_contact(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    digits_only = "".join(ch for ch in text if ch.isdigit())
    if len(digits_only) not in {10, 11}:
        return None
    return digits_only


def _safe_remove(path: Path | None) -> None:
    if path is None:
        return
    try:
        if path.exists():
            path.unlink()
    except OSError:
        return


def _get_booking_patient_name(booking: dict[str, Any]) -> str | None:
    patient_name = booking.get("patient_name")
    if patient_name is not None and str(patient_name).strip():
        return str(patient_name).strip()

    customer_name = booking.get("customer_name")
    if customer_name is not None and str(customer_name).strip():
        return str(customer_name).strip()
    return None


def load_bookings(path: str | Path | None = None) -> list[dict]:
    bookings_path = _resolve_path(path)
    try:
        raw_text = bookings_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise StorageReadError(f"Failed to read bookings from {bookings_path}") from exc

    if not raw_text.strip():
        return []

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise StorageDecodeError(f"Invalid JSON in {bookings_path}") from exc

    if not isinstance(data, list):
        raise StorageDecodeError(f"Expected a JSON array in {bookings_path}")

    invalid_items = [item for item in data if not isinstance(item, dict)]
    if invalid_items:
        raise StorageDecodeError(f"Expected only object records in {bookings_path}")

    return [dict(item) for item in data]


def save_bookings(bookings: list[dict], path: str | Path | None = None) -> bool:
    bookings_path = _resolve_path(path)
    bookings_path.parent.mkdir(parents=True, exist_ok=True)

    temp_path: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=str(bookings_path.parent),
        ) as temp_file:
            json.dump(bookings, temp_file, ensure_ascii=False, indent=2)
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_path = Path(temp_file.name)

        temp_path.replace(bookings_path)
        return True
    except OSError as exc:
        _safe_remove(temp_path)
        raise StorageWriteError(f"Failed to write bookings to {bookings_path}") from exc
    except (TypeError, ValueError) as exc:
        _safe_remove(temp_path)
        raise StorageWriteError(f"Failed to serialize bookings for {bookings_path}") from exc


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


def normalize_birth_date(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    digits_only = "".join(ch for ch in text if ch.isdigit())
    candidate_formats: list[str] = []

    if len(digits_only) == 8:
        candidate_formats.append(f"{digits_only[:4]}-{digits_only[4:6]}-{digits_only[6:8]}")

    normalized_text = (
        text.replace("년", "-")
        .replace("월", "-")
        .replace("일", "")
        .replace(".", "-")
        .replace("/", "-")
        .strip()
    )
    candidate_formats.append(normalized_text)

    for candidate in candidate_formats:
        try:
            return datetime.strptime(candidate, "%Y-%m-%d").date().isoformat()
        except ValueError:
            continue
    return None


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


def _validate_required_field(booking: dict[str, Any], field_name: str) -> None:
    if field_name == "is_proxy_booking":
        if field_name not in booking:
            raise StorageValidationError(f"Missing required booking field: {field_name}")
        return

    value = booking.get(field_name)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise StorageValidationError(f"Missing required booking field: {field_name}")


def _prepare_booking_record(record: dict[str, Any], bookings: list[dict]) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise StorageValidationError("Booking record must be a dictionary")

    now = datetime.now(timezone.utc).isoformat()
    booking = dict(record)

    patient_name = _get_booking_patient_name(booking)
    if not patient_name:
        raise StorageValidationError("Missing required booking field: patient_name")

    normalized_contact = normalize_patient_contact(booking.get("patient_contact"))
    if normalized_contact is None:
        raise StorageValidationError("Missing required booking field: patient_contact")

    booking["id"] = str(booking.get("id") or _next_booking_id(bookings))
    booking["patient_name"] = patient_name
    booking.setdefault("customer_name", patient_name)
    booking["patient_contact"] = str(booking.get("patient_contact")).strip()
    booking["is_proxy_booking"] = bool(booking.get("is_proxy_booking", False))
    booking["status"] = str(booking.get("status") or "active")
    booking["created_at"] = str(booking.get("created_at") or now)
    booking["updated_at"] = now

    normalized_birth_date = normalize_birth_date(booking.get("birth_date"))
    if normalized_birth_date:
        booking["birth_date"] = normalized_birth_date
    elif "birth_date" in booking and booking.get("birth_date") in {"", None}:
        booking.pop("birth_date", None)

    for field_name in REQUIRED_BOOKING_FIELDS:
        _validate_required_field(booking, field_name)

    return booking


def _has_active_duplicate_booking(booking: dict[str, Any], bookings: list[dict]) -> bool:
    booking_contact = normalize_patient_contact(booking.get("patient_contact"))
    booking_time = booking.get("booking_time")
    booking_department = booking.get("department")

    if not booking_contact or not booking_time or not booking_department:
        return False

    for existing_booking in bookings:
        if existing_booking.get("status", "active") != "active":
            continue
        if normalize_patient_contact(existing_booking.get("patient_contact")) != booking_contact:
            continue
        if existing_booking.get("booking_time") != booking_time:
            continue
        if existing_booking.get("department") != booking_department:
            continue
        return True

    return False


def _interpret_recheck_result(result: Any) -> tuple[bool, str | None]:
    if result is None:
        return True, None

    if isinstance(result, dict):
        return bool(result.get("allowed", True)), result.get("reason") or result.get("message")

    if isinstance(result, tuple) and result:
        allowed = bool(result[0])
        reason = str(result[1]) if len(result) > 1 and result[1] is not None else None
        return allowed, reason

    if isinstance(result, bool):
        return result, None

    return bool(result), None


def _recheck_before_persist(
    booking: dict[str, Any],
    bookings: list[dict],
    availability_rechecker: Callable[[dict[str, Any], list[dict]], Any] | None = None,
) -> None:
    if _has_active_duplicate_booking(booking, bookings):
        raise StorageConflictError("이미 동일 환자에 대한 활성 예약이 같은 시간에 존재합니다.")

    if availability_rechecker is None:
        return

    allowed, reason = _interpret_recheck_result(
        availability_rechecker(dict(booking), [dict(item) for item in bookings])
    )
    if not allowed:
        raise StorageConflictError(reason or "최종 저장 직전 저장소 재검증에 실패했습니다.")


def create_booking(
    record: dict[str, Any],
    path: str | Path | None = None,
    *,
    availability_rechecker: Callable[[dict[str, Any], list[dict]], Any] | None = None,
) -> dict:
    bookings = load_bookings(path)
    booking = _prepare_booking_record(record, bookings)
    _recheck_before_persist(booking, bookings, availability_rechecker)
    bookings.append(booking)
    save_bookings(bookings, path)
    return booking


def find_bookings(
    customer_name: str | None = None,
    filters: dict[str, Any] | None = None,
    path: str | Path | None = None,
    include_cancelled: bool = False,
    patient_contact: str | None = None,
) -> list[dict]:
    filters = filters or {}
    results: list[dict] = []
    normalized_birth_date = normalize_birth_date(filters.get("birth_date"))
    requested_status = filters.get("status")
    requested_contact = normalize_patient_contact(patient_contact or filters.get("patient_contact"))

    for booking in load_bookings(path):
        booking_status = booking.get("status", "active")
        if requested_status:
            if booking_status != requested_status:
                continue
        elif not include_cancelled and booking_status != "active":
            continue

        booking_name = _get_booking_patient_name(booking)
        if (
            not requested_contact
            and customer_name
            and booking_name != customer_name
            and booking.get("customer_name") != customer_name
        ):
            continue
        if requested_contact and normalize_patient_contact(booking.get("patient_contact")) != requested_contact:
            continue
        if filters.get("id") and booking.get("id") != filters["id"]:
            continue
        if filters.get("department") and booking.get("department") != filters["department"]:
            continue
        if filters.get("booking_time") and booking.get("booking_time") != filters["booking_time"]:
            continue

        booking_birth_date = normalize_birth_date(booking.get("birth_date"))
        if normalized_birth_date and booking_birth_date != normalized_birth_date:
            continue

        booking_date, booking_time = _extract_booking_date_time(booking)
        if filters.get("date") and booking_date != filters["date"]:
            continue
        if filters.get("time") and booking_time != filters["time"]:
            continue

        results.append(dict(booking))

    return results


def list_patient_birth_dates(
    customer_name: str,
    path: str | Path | None = None,
) -> list[str]:
    birth_dates = {
        normalized_birth_date
        for booking in find_bookings(
            customer_name=customer_name,
            path=path,
            include_cancelled=True,
        )
        if (normalized_birth_date := normalize_birth_date(booking.get("birth_date")))
    }
    return sorted(birth_dates)


def _history_identity_keys(bookings: list[dict[str, Any]]) -> set[tuple[str, str]]:
    identity_keys: set[tuple[str, str]] = set()
    for booking in bookings:
        normalized_contact = normalize_patient_contact(booking.get("patient_contact"))
        normalized_birth_date = normalize_birth_date(booking.get("birth_date"))
        if normalized_contact:
            identity_keys.add(("patient_contact", normalized_contact))
            continue
        if normalized_birth_date:
            identity_keys.add(("birth_date", normalized_birth_date))
            continue
        identity_keys.add(("booking_id", str(booking.get("id") or "unknown")))
    return identity_keys


def _build_customer_type_result(
    matched_bookings: list[dict[str, Any]],
    *,
    ambiguous: bool = False,
    birth_date_candidates: list[str] | None = None,
) -> dict[str, Any]:
    has_non_cancelled_history = any(
        booking.get("status", "active") != "cancelled"
        for booking in matched_bookings
    )
    has_cancelled_history = any(
        booking.get("status", "active") == "cancelled"
        for booking in matched_bookings
    )

    return {
        "customer_type": None if ambiguous else ("재진" if has_non_cancelled_history else "초진"),
        "ambiguous": ambiguous,
        "birth_date_candidates": birth_date_candidates or [],
        "matched_bookings": matched_bookings if not ambiguous else [],
        "has_non_cancelled_history": has_non_cancelled_history if not ambiguous else False,
        "has_cancelled_history": has_cancelled_history if not ambiguous else False,
    }


def resolve_customer_type_from_history(
    customer_name: str | None = None,
    birth_date: str | None = None,
    path: str | Path | None = None,
    patient_contact: str | None = None,
) -> dict[str, Any]:
    normalized_contact = normalize_patient_contact(patient_contact)
    if normalized_contact:
        matched_bookings = find_bookings(
            path=path,
            include_cancelled=True,
            patient_contact=patient_contact,
        )
        return _build_customer_type_result(matched_bookings)

    if not customer_name:
        return {
            "customer_type": None,
            "ambiguous": False,
            "birth_date_candidates": [],
            "matched_bookings": [],
            "has_non_cancelled_history": False,
            "has_cancelled_history": False,
        }

    matched_by_name = find_bookings(
        customer_name=customer_name,
        path=path,
        include_cancelled=True,
    )
    birth_date_candidates = list_patient_birth_dates(customer_name, path=path)
    normalized_birth_date = normalize_birth_date(birth_date)

    if normalized_birth_date:
        matched_bookings = [
            booking
            for booking in matched_by_name
            if normalize_birth_date(booking.get("birth_date")) == normalized_birth_date
        ]
        return _build_customer_type_result(
            matched_bookings,
            birth_date_candidates=birth_date_candidates,
        )

    ambiguous = len(_history_identity_keys(matched_by_name)) > 1
    if ambiguous:
        return _build_customer_type_result(
            [],
            ambiguous=True,
            birth_date_candidates=birth_date_candidates,
        )

    return _build_customer_type_result(
        matched_by_name,
        birth_date_candidates=birth_date_candidates,
    )


def cancel_booking(booking_id: str, path: str | Path | None = None) -> bool:
    if not booking_id:
        return False

    bookings = load_bookings(path)
    now = datetime.now(timezone.utc).isoformat()

    for booking in bookings:
        if booking.get("id") != booking_id:
            continue

        if booking.get("status") == "cancelled":
            return True

        booking["status"] = "cancelled"
        booking["updated_at"] = now
        booking["cancelled_at"] = now
        save_bookings(bookings, path)
        return True

    return False
