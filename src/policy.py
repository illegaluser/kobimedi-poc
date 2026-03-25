from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any

# --- Constants ---
DOCTOR_DEPARTMENT_MAP = {
    "이춘영 원장": "이비인후과",
    "김만수 원장": "내과",
    "원징수 원장": "정형외과",
}

POLICY_REASONS = {
    "SLOT_UNAVAILABLE": "요청하신 시간에는 기존 예약과 시간이 겹쳐 예약할 수 없습니다.",
    "SLOT_FULL_CAPACITY": "해당 1시간대는 최대 예약 인원에 도달했습니다.",
    "CHANGE_WINDOW_EXPIRED": "예약 변경 및 취소는 예약 시간 기준 24시간 전까지만 가능합니다.",
    "NO_EXISTING_APPOINTMENT": "확인, 변경 또는 취소할 기존 예약 정보를 찾을 수 없습니다.",
    "AMBIGUOUS_EXISTING_APPOINTMENT": "대상 예약이 여러 건이어서 어떤 예약인지 추가 확인이 필요합니다.",
    "MISSING_BOOKING_TIME": "예약 날짜와 시간이 확인되지 않아 추가 정보가 필요합니다.",
    "MISSING_CUSTOMER_TYPE": "초진 또는 재진 여부를 확인해야 예약 가능 시간을 판단할 수 있습니다.",
    "PAST_BOOKING_TIME": "이미 지난 시간은 예약할 수 없습니다.",
    "OUTSIDE_BUSINESS_HOURS": "운영시간 내에서만 예약할 수 있습니다.",
    "LUNCH_BREAK": "점심시간(12:30-13:30)과 겹쳐 예약할 수 없습니다.",
    "CLOSED_SUNDAY": "일요일은 휴진입니다.",
    "HOLIDAY_CLOSED": "공휴일은 휴진입니다.",
    "HOLIDAY_UNCERTAIN": "해당 날짜의 공휴일 여부가 확인되지 않아 추가 확인이 필요합니다.",
    "SAME_DAY_EMERGENCY_ESCALATION": "당일 긴급 진료 요청은 자동 예약이 아니라 상담원 또는 의료진 확인이 먼저 필요합니다.",
    "SUCCESS": "정책 검사를 통과했습니다.",
}

APPOINTMENT_DURATION_MINS = {
    "초진": 40,
    "재진": 30,
}
DEFAULT_APPOINTMENT_DURATION_MINS = 30
MAX_APPOINTMENTS_PER_HOUR = 3
ALTERNATIVE_SLOT_LIMIT = 3
ALTERNATIVE_SLOT_INCREMENT_MINUTES = 10
ALTERNATIVE_SLOT_SEARCH_DAYS = 14

WEEKDAY_OPEN = time(9, 0)
WEEKDAY_CLOSE = time(18, 0)
SATURDAY_OPEN = time(9, 0)
SATURDAY_CLOSE = time(13, 0)
LUNCH_START = time(12, 30)
LUNCH_END = time(13, 30)


# --- Helper Functions ---
def _normalize_datetime(value: Any, reference_tz=None) -> datetime | None:
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


def _round_up_to_next_increment(dt: datetime, increment_minutes: int = ALTERNATIVE_SLOT_INCREMENT_MINUTES) -> datetime:
    boundary = dt.replace(second=0, microsecond=0)
    remainder = boundary.minute % increment_minutes
    if remainder:
        boundary += timedelta(minutes=increment_minutes - remainder)
    if boundary <= dt:
        boundary += timedelta(minutes=increment_minutes)
    return boundary.replace(second=0, microsecond=0)


def _reason_code_from_text(reason_text: str) -> str:
    for code, text_value in POLICY_REASONS.items():
        if text_value == reason_text:
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


def _extract_requested_start(intent: dict, now: datetime) -> datetime | None:
    booking_time = intent.get("booking_time")
    if booking_time:
        return _normalize_datetime(booking_time, now.tzinfo)

    date_value = intent.get("date")
    time_value = intent.get("time")
    if not date_value or not time_value:
        return None
    return _normalize_datetime(f"{str(date_value).strip()}T{str(time_value).strip()}", now.tzinfo)


def _extract_appointment_start(appointment: dict, now: datetime) -> datetime | None:
    if not appointment:
        return None

    if appointment.get("booking_time"):
        return _normalize_datetime(appointment.get("booking_time"), now.tzinfo)

    if appointment.get("date") and appointment.get("time"):
        return _normalize_datetime(f"{appointment['date']}T{appointment['time']}", now.tzinfo)

    return None


def _extract_appointment_duration(appointment: dict) -> int:
    return get_appointment_duration(appointment.get("customer_type")) or DEFAULT_APPOINTMENT_DURATION_MINS


def _is_active_appointment(appointment: dict) -> bool:
    return appointment.get("status", "active") == "active"


def _is_same_local_day(target: datetime, reference: datetime) -> bool:
    comparison_tz = target.tzinfo or reference.tzinfo or timezone.utc
    localized_target = target.astimezone(comparison_tz)
    localized_reference = reference.astimezone(comparison_tz)
    return localized_target.date() == localized_reference.date()


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
        appointment.get("patient_contact"),
        appointment.get("booking_time"),
        appointment.get("date"),
        appointment.get("time"),
        appointment.get("department"),
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


def _combine_local_time(base: datetime, local_time: time) -> datetime:
    return base.replace(
        hour=local_time.hour,
        minute=local_time.minute,
        second=0,
        microsecond=0,
    )


def _resolve_holiday_status(requested_start: datetime, intent: dict | None, now: datetime) -> str:
    del now
    if not intent:
        return "open"

    holiday_status = intent.get("holiday_status")
    if isinstance(holiday_status, bool):
        return "closed" if holiday_status else "open"
    if isinstance(holiday_status, str):
        normalized = holiday_status.strip().lower()
        if normalized in {"closed", "holiday", "public_holiday", "true"}:
            return "closed"
        if normalized in {"open", "not_holiday", "false"}:
            return "open"
        if normalized in {"unknown", "uncertain", "clarify"}:
            return "unknown"

    public_holiday = intent.get("is_public_holiday")
    if isinstance(public_holiday, bool):
        return "closed" if public_holiday else "open"

    holiday_calendar = intent.get("holiday_calendar")
    requested_date = requested_start.date().isoformat()
    if isinstance(holiday_calendar, dict):
        if requested_date in holiday_calendar:
            value = holiday_calendar[requested_date]
            if value in {True, "closed", "holiday", "public_holiday"}:
                return "closed"
            if value in {False, "open", "not_holiday"}:
                return "open"
            return "unknown"
        return "open"
    if isinstance(holiday_calendar, (list, tuple, set)):
        return "closed" if requested_date in {str(item) for item in holiday_calendar} else "open"

    if intent.get("holiday_known") is False or intent.get("holiday_uncertain"):
        return "unknown"
    if intent.get("mentions_public_holiday"):
        return "unknown"

    return "open"


def _validate_operating_hours(
    requested_start: datetime,
    duration_mins: int,
    now: datetime,
    *,
    intent: dict | None = None,
) -> tuple[bool, str]:
    local_tz = requested_start.tzinfo or now.tzinfo or timezone.utc
    local_start = requested_start.astimezone(local_tz)
    local_now = now.astimezone(local_tz)
    requested_end = local_start + timedelta(minutes=duration_mins)

    if local_start < local_now:
        return False, POLICY_REASONS["PAST_BOOKING_TIME"]

    holiday_status = _resolve_holiday_status(local_start, intent, now)
    if holiday_status == "unknown":
        return False, POLICY_REASONS["HOLIDAY_UNCERTAIN"]
    if holiday_status == "closed":
        return False, POLICY_REASONS["HOLIDAY_CLOSED"]

    weekday = local_start.weekday()
    if weekday == 6:
        return False, POLICY_REASONS["CLOSED_SUNDAY"]

    if weekday == 5:
        open_time, close_time = SATURDAY_OPEN, SATURDAY_CLOSE
    else:
        open_time, close_time = WEEKDAY_OPEN, WEEKDAY_CLOSE

    open_dt = _combine_local_time(local_start, open_time)
    close_dt = _combine_local_time(local_start, close_time)
    lunch_start_dt = _combine_local_time(local_start, LUNCH_START)
    lunch_end_dt = _combine_local_time(local_start, LUNCH_END)

    if local_start < open_dt or requested_end > close_dt:
        return False, POLICY_REASONS["OUTSIDE_BUSINESS_HOURS"]

    if local_start < lunch_end_dt and requested_end > lunch_start_dt:
        return False, POLICY_REASONS["LUNCH_BREAK"]

    return True, POLICY_REASONS["SUCCESS"]


def _build_storage_lookup_filters(intent: dict, now: datetime) -> tuple[str | None, dict]:
    requested_start = _extract_requested_start(intent, now)
    target_hint = intent.get("target_appointment_hint") or {}

    customer_name = intent.get("customer_name") or target_hint.get("customer_name")

    filters: dict[str, Any] = {}
    booking_id = intent.get("booking_id") or target_hint.get("id")
    if booking_id:
        filters["id"] = booking_id

    department = intent.get("department") or target_hint.get("department")
    if department:
        filters["department"] = department

    patient_contact = intent.get("patient_contact") or target_hint.get("patient_contact")
    if patient_contact:
        filters["patient_contact"] = patient_contact

    birth_date = intent.get("birth_date") or target_hint.get("birth_date")
    if birth_date:
        filters["birth_date"] = birth_date

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
    now: datetime,
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
    patient_contact = filters.get("patient_contact")

    attempts = [
        lambda: finder(customer_name=customer_name, filters=filters, patient_contact=patient_contact),
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


def _should_offer_alternatives(reason_code: str) -> bool:
    return reason_code in {
        "PAST_BOOKING_TIME",
        "OUTSIDE_BUSINESS_HOURS",
        "LUNCH_BREAK",
        "CLOSED_SUNDAY",
        "HOLIDAY_CLOSED",
        "SLOT_FULL_CAPACITY",
        "SLOT_UNAVAILABLE",
    }


def _build_slot_rejection_result(
    *,
    reason_code: str,
    requested_start: datetime,
    customer_type: str,
    all_appointments: list[dict],
    now: datetime,
    intent: dict | None = None,
) -> dict:
    alternatives = []
    if _should_offer_alternatives(reason_code):
        alternatives = suggest_alternative_slots(
            requested_start,
            customer_type,
            all_appointments,
            limit=ALTERNATIVE_SLOT_LIMIT,
            now=now,
            holiday_context=intent,
        )
    return _policy_result(
        False,
        reason_code,
        recommended_action="clarify",
        needs_alternative=bool(alternatives),
        alternative_slots=alternatives,
        slot_duration_minutes=get_appointment_duration(customer_type),
    )


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
    return (appointment_time - now).total_seconds() >= 86400


def check_hourly_capacity(
    requested_start: datetime | str,
    all_appointments: list[dict],
    now: datetime,
) -> tuple[bool, str | None]:
    requested_start_dt = _normalize_datetime(requested_start, now.tzinfo)
    if requested_start_dt is None:
        return False, POLICY_REASONS["MISSING_BOOKING_TIME"]

    local_tz = requested_start_dt.tzinfo or now.tzinfo or timezone.utc
    local_start = requested_start_dt.astimezone(local_tz)
    window_start = local_start.replace(minute=0, second=0, microsecond=0)
    window_end = window_start + timedelta(hours=1)
    appointments_in_window = 0

    for appointment in all_appointments:
        if not _is_active_appointment(appointment):
            continue
        appointment_start = _extract_appointment_start(appointment, now)
        if appointment_start is None:
            continue
        localized = appointment_start.astimezone(local_tz)
        if window_start <= localized < window_end:
            appointments_in_window += 1

    if appointments_in_window >= MAX_APPOINTMENTS_PER_HOUR:
        return False, POLICY_REASONS["SLOT_FULL_CAPACITY"]
    return True, POLICY_REASONS["SUCCESS"]


def is_slot_available(
    requested_start_str: str | datetime,
    customer_type: str | None,
    existing_appointments: list[dict],
    now: datetime,
    *,
    holiday_context: dict | None = None,
) -> tuple[bool, str]:
    requested_start = _normalize_datetime(requested_start_str, now.tzinfo)
    if requested_start is None:
        return False, POLICY_REASONS["MISSING_BOOKING_TIME"]

    duration_mins = get_appointment_duration(customer_type)
    if duration_mins is None:
        return False, POLICY_REASONS["MISSING_CUSTOMER_TYPE"]

    within_hours, hours_reason = _validate_operating_hours(
        requested_start,
        duration_mins,
        now,
        intent=holiday_context,
    )
    if not within_hours:
        return False, hours_reason

    has_capacity, capacity_reason = check_hourly_capacity(requested_start, existing_appointments, now)
    if not has_capacity:
        return False, capacity_reason

    requested_end = requested_start + timedelta(minutes=duration_mins)
    for appointment in existing_appointments:
        if not _is_active_appointment(appointment):
            continue
        appointment_start = _extract_appointment_start(appointment, now)
        if appointment_start is None:
            continue
        appointment_duration = _extract_appointment_duration(appointment)
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

    resolved_now = now or datetime.now(timezone.utc)
    resolved_candidates = _find_existing_appointments_via_storage(storage, intent, resolved_now)
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

    return _policy_result(
        True,
        "SUCCESS",
        recommended_action=action,
        existing_appointment=resolved_existing_appointment,
    )


def evaluate_same_day_booking(intent: dict, now: datetime) -> dict:
    requested_start = _extract_requested_start(intent, now)
    if requested_start is None:
        return _policy_result(False, "MISSING_BOOKING_TIME", recommended_action="clarify")

    if not _is_same_local_day(requested_start, now):
        return _policy_result(True, "SUCCESS", recommended_action="book_appointment", same_day=False)

    if _is_emergency_intent(intent):
        return _policy_result(
            False,
            "SAME_DAY_EMERGENCY_ESCALATION",
            recommended_action="escalate",
            same_day=True,
        )

    return _policy_result(True, "SUCCESS", recommended_action="book_appointment", same_day=True)


def suggest_alternative_slots(
    requested_start_str: str | datetime,
    customer_type: str | None,
    all_appointments: list[dict],
    limit: int = ALTERNATIVE_SLOT_LIMIT,
    now: datetime | None = None,
    *,
    holiday_context: dict | None = None,
) -> list[str]:
    if now is None:
        now = datetime.now(timezone.utc)

    requested_start = _normalize_datetime(requested_start_str, now.tzinfo)
    duration_mins = get_appointment_duration(customer_type)
    if requested_start is None or duration_mins is None:
        return []

    alternatives: list[str] = []
    seen: set[str] = set()
    search_start = requested_start if requested_start >= now else now
    candidate_start = _round_up_to_next_increment(search_start)
    search_deadline = candidate_start + timedelta(days=ALTERNATIVE_SLOT_SEARCH_DAYS)

    while candidate_start <= search_deadline and len(alternatives) < limit:
        allowed, _reason = is_slot_available(
            candidate_start,
            customer_type,
            all_appointments,
            now,
            holiday_context=holiday_context,
        )
        if allowed:
            formatted = _format_datetime(candidate_start)
            if formatted not in seen:
                alternatives.append(formatted)
                seen.add(formatted)
        candidate_start += timedelta(minutes=ALTERNATIVE_SLOT_INCREMENT_MINUTES)

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
    """Apply deterministic booking policy without any LLM delegation."""
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
            same_day_result["slot_duration_minutes"] = duration_mins
            return same_day_result

        allowed, reason = is_slot_available(
            requested_start,
            customer_type,
            all_appointments,
            now,
            holiday_context=intent,
        )
        if not allowed:
            return _build_slot_rejection_result(
                reason_code=_reason_code_from_text(reason),
                requested_start=requested_start,
                customer_type=customer_type,
                all_appointments=all_appointments,
                now=now,
                intent=intent,
            )

        return _policy_result(
            True,
            "SUCCESS",
            recommended_action="book_appointment",
            slot_duration_minutes=duration_mins,
            same_day=same_day_result.get("same_day", False),
        )

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
            existing_start = _extract_appointment_start(resolved_existing_appointment, now)
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
            allowed, reason = is_slot_available(
                requested_start,
                customer_type,
                other_appointments,
                now,
                holiday_context=intent,
            )
            if not allowed:
                return _build_slot_rejection_result(
                    reason_code=_reason_code_from_text(reason),
                    requested_start=requested_start,
                    customer_type=customer_type,
                    all_appointments=other_appointments,
                    now=now,
                    intent=intent,
                )

            return _policy_result(
                True,
                "SUCCESS",
                recommended_action="modify_appointment",
                slot_duration_minutes=duration_mins,
            )

        return _policy_result(True, "SUCCESS", recommended_action=action)

    return _policy_result(True, "SUCCESS", recommended_action=action)