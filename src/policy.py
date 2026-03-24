
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

# --- Constants ---
DOCTOR_DEPARTMENT_MAP = {
    "이춘영 원장": "이비인후과",
    "김만수 원장": "내과",
    "원징수 원장": "정형외과",
}

POLICY_REASONS = {
    "SLOT_UNAVAILABLE": "요청하신 시간에는 예약이 이미 가득 찼습니다. 다른 시간을 선택해 주세요.",
    "SLOT_FULL_CAPACITY": "해당 시간대에는 예약 인원이 가득 찼습니다. 다른 시간대를 선택해 주세요.",
    "CHANGE_WINDOW_EXPIRED": "예약 변경 및 취소는 예약 시간 기준 24시간 전까지만 가능합니다.",
    "NO_EXISTING_APPOINTMENT": "확인, 변경 또는 취소할 기존 예약 정보를 찾을 수 없습니다.",
    "AMBIGUOUS_EXISTING_APPOINTMENT": "대상 예약이 여러 건이어서 어떤 예약인지 추가 확인이 필요합니다.",
    "MISSING_BOOKING_TIME": "예약 날짜와 시간이 확인되지 않아 추가 정보가 필요합니다.",
    "MISSING_CUSTOMER_TYPE": "초진 또는 재진 여부를 확인해야 예약 가능 시간을 판단할 수 있습니다.",
    "SAME_DAY_BOOKING_REQUIRES_CONFIRMATION": "일반적인 당일 신규 예약은 자동 확정할 수 없습니다. 다시 한 번 확인하거나 다른 시간대를 선택해 주세요.",
    "SAME_DAY_EMERGENCY_ESCALATION": "당일 긴급 진료 요청은 자동 예약이 아니라 상담원 또는 의료진 확인이 먼저 필요합니다.",
    "SUCCESS": "정책 검사를 통과했습니다.",
}

APPOINTMENT_DURATION_MINS = {
    "초진": 40,
    "재진": 30,
}
DEFAULT_APPOINTMENT_DURATION_MINS = 30
MAX_APPOINTMENTS_PER_HOUR = 3
ALTERNATIVE_SLOT_SEARCH_LIMIT = 48


# --- Helper Functions ---
def _normalize_datetime(value, reference_tz=None) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        dt = value
    else:
        raw_value = str(value).strip()
        if not raw_value:
            return None
        if raw_value.endswith("Z"):
            raw_value = f"{raw_value[:-1]}+00:00"
        try:
            dt = datetime.fromisoformat(raw_value)
        except ValueError:
            return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=reference_tz or timezone.utc)
    return dt


def _format_datetime(dt: datetime) -> str:
    if dt.utcoffset() == timedelta(0):
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return dt.isoformat()


def _round_up_to_next_slot_boundary(dt: datetime, slot_minutes: int = 30) -> datetime:
    minute_bucket = (dt.minute // slot_minutes) * slot_minutes
    boundary = dt.replace(minute=minute_bucket, second=0, microsecond=0)
    if boundary <= dt:
        boundary += timedelta(minutes=slot_minutes)
    return boundary


def _reason_code_from_text(reason_text: str) -> str:
    for code, text in POLICY_REASONS.items():
        if text == reason_text:
            return code
    return "SUCCESS"


def _policy_result(
    allowed: bool,
    reason_code: str,
    *,
    recommended_action: str | None = None,
    needs_alternative: bool = False,
    alternative_slots: list[str] | None = None,
    **extra,
) -> dict:
    result = {
        "allowed": allowed,
        "reason_code": reason_code,
        "reason": POLICY_REASONS[reason_code],
        "recommended_action": recommended_action,
        "needs_alternative": needs_alternative,
        "alternative_slots": alternative_slots or [],
    }
    result.update(extra)
    return result


def _normalize_appointments_result(raw_result: Any) -> list[dict] | None:
    if raw_result is None:
        return []
    if isinstance(raw_result, dict):
        return [raw_result]
    if isinstance(raw_result, (list, tuple)):
        return [item for item in raw_result if isinstance(item, dict)]
    return None


def _build_storage_lookup_filters(intent: dict, now: datetime | None = None) -> tuple[str | None, dict]:
    requested_start = _extract_requested_start(intent, now)
    target_hint = intent.get("target_appointment_hint") or {}

    customer_name = (
        intent.get("customer_name")
        or target_hint.get("customer_name")
    )

    filters: dict[str, Any] = {}
    booking_id = intent.get("booking_id") or target_hint.get("id")
    if booking_id:
        filters["id"] = booking_id

    department = intent.get("department") or target_hint.get("department")
    if department:
        filters["department"] = department

    if requested_start is not None:
        filters["booking_time"] = _format_datetime(requested_start)
        filters["date"] = requested_start.date().isoformat()
        filters["time"] = requested_start.strftime("%H:%M")
    else:
        date_value = intent.get("date") or target_hint.get("date")
        time_value = intent.get("time") or target_hint.get("time")
        if date_value:
            filters["date"] = str(date_value)
        if time_value:
            filters["time"] = str(time_value)

    return customer_name, filters


def _find_existing_appointments_via_storage(
    storage: Any,
    intent: dict | None,
    now: datetime | None = None,
) -> list[dict] | None:
    if storage is None:
        return None

    finder = getattr(storage, "find_bookings", None)
    if finder is None and isinstance(storage, dict):
        finder = storage.get("find_bookings")
    if not callable(finder):
        return None

    lookup_intent = intent or {}
    customer_name, filters = _build_storage_lookup_filters(lookup_intent, now)
    attempts = [
        lambda: finder(customer_name=customer_name, filters=filters),
        lambda: finder(customer_name=customer_name, **filters),
        lambda: finder(customer_name, filters),
        lambda: finder(filters),
        lambda: finder(),
    ]

    for attempt in attempts:
        try:
            normalized = _normalize_appointments_result(attempt())
        except TypeError:
            continue
        if normalized is not None:
            return normalized

    return None


def _extract_requested_start(intent: dict, now: datetime | None = None) -> datetime | None:
    reference_now = now or datetime.now(timezone.utc)
    booking_time = intent.get("booking_time")
    if booking_time:
        return _normalize_datetime(booking_time, reference_now.tzinfo)

    date = intent.get("date")
    time_value = intent.get("time")
    if not date or not time_value:
        return None
    return _normalize_datetime(f"{str(date).strip()}T{str(time_value).strip()}", reference_now.tzinfo)


def _extract_appointment_start(appointment: dict, reference_tz=None) -> datetime | None:
    if not appointment:
        return None

    if appointment.get("booking_time"):
        return _normalize_datetime(appointment.get("booking_time"), reference_tz)

    if appointment.get("date") and appointment.get("time"):
        return _normalize_datetime(f"{appointment['date']}T{appointment['time']}", reference_tz)

    return None


def _is_same_local_day(target: datetime, reference: datetime) -> bool:
    if reference.tzinfo is not None:
        target = target.astimezone(reference.tzinfo)
    elif target.tzinfo is not None:
        reference = reference.replace(tzinfo=target.tzinfo)
    return target.date() == reference.date()


def _is_emergency_intent(intent: dict) -> bool:
    if intent.get("is_emergency") or intent.get("emergency") or intent.get("urgent"):
        return True

    urgency = str(intent.get("urgency") or "").strip().lower()
    if urgency in {"emergency", "urgent", "acute"}:
        return True

    return intent.get("action") == "escalate"


def _appointment_identity(appointment: dict) -> tuple:
    return (
        appointment.get("id"),
        appointment.get("customer_id"),
        appointment.get("booking_time"),
        appointment.get("date"),
        appointment.get("time"),
    )


def _exclude_existing_appointment(all_appointments: list[dict], existing_appointment: dict | None) -> list[dict]:
    if not existing_appointment:
        return list(all_appointments)

    filtered: list[dict] = []
    skipped = False
    existing_identity = _appointment_identity(existing_appointment)
    for appointment in all_appointments:
        if not skipped and (
            appointment is existing_appointment
            or _appointment_identity(appointment) == existing_identity
        ):
            skipped = True
            continue
        filtered.append(appointment)
    return filtered


# --- Policy Check Functions ---
def get_appointment_duration(customer_type: str | None) -> int | None:
    if not customer_type:
        return None
    return APPOINTMENT_DURATION_MINS.get(str(customer_type).strip())


def is_change_allowed(appointment_time_str: str | datetime, now: datetime) -> bool:
    """Exactly 24 hours before is allowed; less than 24 hours is not."""
    appointment_time = _normalize_datetime(appointment_time_str, now.tzinfo)
    if appointment_time is None:
        return False
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now <= (appointment_time - timedelta(hours=24))


def check_hourly_capacity(
    requested_start: datetime | str,
    all_appointments: list[dict],
    now: datetime | None = None,
) -> tuple[bool, str | None]:
    reference_tz = (now.tzinfo if now is not None else timezone.utc) or timezone.utc
    requested_start_dt = _normalize_datetime(requested_start, reference_tz)
    if requested_start_dt is None:
        return False, POLICY_REASONS["MISSING_BOOKING_TIME"]

    window_start = requested_start_dt.replace(minute=0, second=0, microsecond=0)
    window_end = window_start + timedelta(hours=1)
    appointments_in_window = 0

    for appointment in all_appointments:
        appointment_start = _extract_appointment_start(appointment, requested_start_dt.tzinfo)
        if appointment_start is None:
            continue
        localized = appointment_start.astimezone(requested_start_dt.tzinfo)
        if window_start <= localized < window_end:
            appointments_in_window += 1

    if appointments_in_window >= MAX_APPOINTMENTS_PER_HOUR:
        return False, POLICY_REASONS["SLOT_FULL_CAPACITY"]
    return True, POLICY_REASONS["SUCCESS"]


def is_slot_available(
    requested_start_str: str | datetime,
    customer_type: str | None,
    existing_appointments: list[dict],
    now: datetime | None = None,
) -> tuple[bool, str]:
    reference_tz = (now.tzinfo if now is not None else timezone.utc) or timezone.utc
    requested_start = _normalize_datetime(requested_start_str, reference_tz)
    if requested_start is None:
        return False, POLICY_REASONS["MISSING_BOOKING_TIME"]

    duration_mins = get_appointment_duration(customer_type)
    if duration_mins is None:
        return False, POLICY_REASONS["MISSING_CUSTOMER_TYPE"]

    has_capacity, capacity_reason = check_hourly_capacity(requested_start, existing_appointments, now=now)
    if not has_capacity:
        return False, capacity_reason

    requested_end = requested_start + timedelta(minutes=duration_mins)

    for appointment in existing_appointments:
        appointment_start = _extract_appointment_start(appointment, requested_start.tzinfo)
        if appointment_start is None:
            continue
        appointment_duration = get_appointment_duration(appointment.get("customer_type")) or DEFAULT_APPOINTMENT_DURATION_MINS
        appointment_end = appointment_start + timedelta(minutes=appointment_duration)
        if requested_start < appointment_end and requested_end > appointment_start:
            return False, POLICY_REASONS["SLOT_UNAVAILABLE"]

    return True, POLICY_REASONS["SUCCESS"]


def validate_existing_appointment(
    action: str,
    existing_appointment: dict | None,
    candidate_appointments: list[dict] | None = None,
    *,
    storage: Any = None,
    intent: dict | None = None,
    now: datetime | None = None,
) -> dict:
    if action not in {"modify_appointment", "cancel_appointment", "check_appointment"}:
        return _policy_result(True, "SUCCESS", recommended_action=action)

    resolved_candidates = _find_existing_appointments_via_storage(storage, intent, now)
    if resolved_candidates is None:
        resolved_candidates = candidate_appointments
    if resolved_candidates and len(resolved_candidates) > 1:
        return _policy_result(
            False,
            "AMBIGUOUS_EXISTING_APPOINTMENT",
            recommended_action="clarify",
            candidate_appointments=resolved_candidates,
        )

    resolved_existing_appointment = existing_appointment
    if resolved_candidates is not None:
        resolved_existing_appointment = resolved_candidates[0] if resolved_candidates else None

    if not resolved_existing_appointment:
        return _policy_result(False, "NO_EXISTING_APPOINTMENT", recommended_action="clarify")

    return _policy_result(True, "SUCCESS", recommended_action=action, existing_appointment=resolved_existing_appointment)


def evaluate_same_day_booking(intent: dict, now: datetime) -> dict:
    requested_start = _extract_requested_start(intent, now)
    if requested_start is None:
        return _policy_result(False, "MISSING_BOOKING_TIME", recommended_action="clarify")

    if not _is_same_local_day(requested_start, now):
        return _policy_result(True, "SUCCESS", recommended_action="book_appointment")

    if _is_emergency_intent(intent):
        return _policy_result(False, "SAME_DAY_EMERGENCY_ESCALATION", recommended_action="escalate", same_day=True)

    return _policy_result(False, "SAME_DAY_BOOKING_REQUIRES_CONFIRMATION", recommended_action="clarify", same_day=True)


def suggest_alternative_slots(
    requested_start_str: str | datetime,
    customer_type: str | None,
    all_appointments: list[dict],
    limit: int = 3,
    now: datetime | None = None,
) -> list[str]:
    reference_tz = (now.tzinfo if now is not None else timezone.utc) or timezone.utc
    requested_start = _normalize_datetime(requested_start_str, reference_tz)
    if requested_start is None or get_appointment_duration(customer_type) is None:
        return []

    alternatives: list[str] = []
    seen: set[str] = set()
    candidate_start = _round_up_to_next_slot_boundary(requested_start)

    for _ in range(ALTERNATIVE_SLOT_SEARCH_LIMIT):
        allowed, _reason = is_slot_available(candidate_start, customer_type, all_appointments, now=now)
        if allowed:
            formatted = _format_datetime(candidate_start)
            if formatted not in seen:
                alternatives.append(formatted)
                seen.add(formatted)
                if len(alternatives) >= limit:
                    break
        candidate_start += timedelta(minutes=30)

    return alternatives


# --- Main Policy Application Function ---
def apply_policy(
    intent: dict,
    existing_appointment: dict | None,
    all_appointments: list[dict] | None,
    now: datetime,
    *,
    storage: Any = None,
    candidate_appointments: list[dict] | None = None,
) -> dict:
    """Apply deterministic booking policy without any Ollama calls."""
    if all_appointments is None:
        all_appointments = []
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    action = intent.get("action")

    if action == "book_appointment":
        requested_start = _extract_requested_start(intent, now)
        customer_type = intent.get("customer_type")
        duration_mins = get_appointment_duration(customer_type)

        if requested_start is None:
            return _policy_result(False, "MISSING_BOOKING_TIME", recommended_action="clarify")
        if duration_mins is None:
            return _policy_result(False, "MISSING_CUSTOMER_TYPE", recommended_action="clarify")

        same_day_result = evaluate_same_day_booking(intent, now)
        if not same_day_result["allowed"]:
            alternatives: list[str] = []
            if same_day_result.get("recommended_action") == "clarify":
                alternatives = suggest_alternative_slots(
                    requested_start + timedelta(days=1),
                    customer_type,
                    all_appointments,
                    now=now,
                )
            same_day_result["needs_alternative"] = bool(alternatives)
            same_day_result["alternative_slots"] = alternatives
            same_day_result["slot_duration_minutes"] = duration_mins
            return same_day_result

        allowed, reason = is_slot_available(requested_start, customer_type, all_appointments, now=now)
        if not allowed:
            alternatives = suggest_alternative_slots(requested_start, customer_type, all_appointments, now=now)
            return _policy_result(
                False,
                _reason_code_from_text(reason),
                recommended_action="clarify",
                needs_alternative=bool(alternatives),
                alternative_slots=alternatives,
                slot_duration_minutes=duration_mins,
            )

        return _policy_result(True, "SUCCESS", recommended_action="book_appointment", slot_duration_minutes=duration_mins)

    if action in {"modify_appointment", "cancel_appointment", "check_appointment"}:
        validation = validate_existing_appointment(
            action,
            existing_appointment,
            candidate_appointments=candidate_appointments,
            storage=storage,
            intent=intent,
            now=now,
        )
        if not validation["allowed"]:
            return validation
        resolved_existing_appointment = validation.get("existing_appointment") or existing_appointment

        if action in {"modify_appointment", "cancel_appointment"}:
            existing_start = _extract_appointment_start(resolved_existing_appointment, now.tzinfo)
            if existing_start is None or not is_change_allowed(existing_start, now):
                return _policy_result(False, "CHANGE_WINDOW_EXPIRED", recommended_action=action)

        if action == "modify_appointment":
            requested_start = _extract_requested_start(intent, now)
            if requested_start is None:
                return _policy_result(False, "MISSING_BOOKING_TIME", recommended_action="clarify")

            customer_type = intent.get("customer_type") or resolved_existing_appointment.get("customer_type")
            duration_mins = get_appointment_duration(customer_type)
            if duration_mins is None:
                return _policy_result(False, "MISSING_CUSTOMER_TYPE", recommended_action="clarify")

            other_appointments = _exclude_existing_appointment(all_appointments, resolved_existing_appointment)
            allowed, reason = is_slot_available(requested_start, customer_type, other_appointments, now=now)
            if not allowed:
                alternatives = suggest_alternative_slots(requested_start, customer_type, other_appointments, now=now)
                return _policy_result(
                    False,
                    _reason_code_from_text(reason),
                    recommended_action="clarify",
                    needs_alternative=bool(alternatives),
                    alternative_slots=alternatives,
                    slot_duration_minutes=duration_mins,
                )

            return _policy_result(True, "SUCCESS", recommended_action="modify_appointment", slot_duration_minutes=duration_mins)

        return _policy_result(True, "SUCCESS", recommended_action=action)

    return _policy_result(True, "SUCCESS", recommended_action=action)

