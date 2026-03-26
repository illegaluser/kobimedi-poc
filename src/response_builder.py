
from __future__ import annotations

from datetime import datetime, timezone

from .classifier import SUPPORTED_DOCTORS


DOCTOR_DEPARTMENT_MAP = SUPPORTED_DOCTORS
DEPARTMENT_DOCTOR_MAP = {department: doctor for doctor, department in DOCTOR_DEPARTMENT_MAP.items()}


def build_response(action: str, message: str, **kwargs) -> dict:
    response = {
        "action": action,
        "response": message,
    }
    response.update(kwargs)
    return response


def _ensure_datetime(value: str | datetime | None, now: datetime | None = None) -> datetime | None:
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

    reference_tz = now.tzinfo if now and now.tzinfo else timezone.utc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=reference_tz)
    return dt


def _format_date_phrase(dt: datetime | None, now: datetime | None = None) -> str:
    if dt is None:
        return ""

    reference = now if now else datetime.now(dt.tzinfo or timezone.utc)
    if reference.tzinfo and dt.tzinfo:
        localized = dt.astimezone(reference.tzinfo)
    else:
        localized = dt

    prefix = f"{localized.month}/{localized.day}"
    delta_days = (localized.date() - reference.date()).days
    if delta_days == 0:
        return f"오늘({prefix})"
    if delta_days == 1:
        return f"내일({prefix})"
    if delta_days == 2:
        return f"모레({prefix})"
    return prefix


def _format_time_phrase(dt: datetime | None) -> str:
    if dt is None:
        return ""
    hour = dt.hour
    minute = dt.minute

    if hour == 0:
        meridiem = "오전"
        display_hour = 12
    elif hour < 12:
        meridiem = "오전"
        display_hour = hour
    elif hour == 12:
        meridiem = "오후"
        display_hour = 12
    else:
        meridiem = "오후"
        display_hour = hour - 12

    if minute == 0:
        return f"{meridiem} {display_hour}시"
    return f"{meridiem} {display_hour}시 {minute}분"


def format_appointment_summary(appointment: dict, now: datetime | None = None) -> str:
    department = appointment.get("department")
    doctor_name = appointment.get("doctor_name") or DEPARTMENT_DOCTOR_MAP.get(department)
    booking_time = appointment.get("booking_time")

    if booking_time is None and appointment.get("date") and appointment.get("time"):
        booking_time = f"{appointment['date']}T{appointment['time']}:00"

    dt = _ensure_datetime(booking_time, now)
    date_text = _format_date_phrase(dt, now) if dt else appointment.get("date") or "날짜 미정"
    time_text = _format_time_phrase(dt) if dt else appointment.get("time") or "시간 미정"

    pieces = [date_text, time_text]
    if department:
        pieces.append(department)
    if doctor_name:
        pieces.append(f"{doctor_name} 진료")
    return " ".join(piece for piece in pieces if piece)


def build_missing_info_question(
    missing_fields: list[str],
    *,
    department: str | None = None,
    action_context: str | None = None,
    customer_type: str | None = None,
) -> str:
    missing_fields = missing_fields or []
    primary = missing_fields[0] if missing_fields else None

    action = action_context or ""

    if primary == "is_proxy_booking":
        if action == "modify_appointment":
            return "예약 변경을 요청하시는 분이 환자 본인이신가요, 아니면 가족이나 지인을 대신하여 요청하시는 건가요?"
        if action == "cancel_appointment":
            return "예약 취소를 요청하시는 분이 환자 본인이신가요, 아니면 가족이나 지인을 대신하여 요청하시는 건가요?"
        if action == "check_appointment":
            return "예약 확인을 요청하시는 분이 환자 본인이신가요, 아니면 가족이나 지인을 대신하여 요청하시는 건가요?"
        # book_appointment or default
        return "예약하시는 분이 환자 본인이신가요, 아니면 가족이나 지인을 대신하여 예약하시는 건가요?"

    if primary == "patient_name":
        has_contact_missing = "patient_contact" in missing_fields
        if action == "modify_appointment":
            if has_contact_missing:
                return "예약 변경을 위해 환자분 성함과 연락처를 함께 알려주세요. (예: 홍길동 010-1234-5678)"
            return "예약 변경을 위해 환자분 성함을 알려주세요."
        if action == "cancel_appointment":
            if has_contact_missing:
                return "예약 취소를 위해 환자분 성함과 연락처를 함께 알려주세요. (예: 홍길동 010-1234-5678)"
            return "예약 취소를 위해 환자분 성함을 알려주세요."
        if action == "check_appointment":
            if has_contact_missing:
                return "예약 확인을 위해 환자분 성함과 연락처를 함께 알려주세요. (예: 홍길동 010-1234-5678)"
            return "예약 확인을 위해 환자분 성함을 알려주세요."
        # book_appointment or default
        if has_contact_missing:
            return "예약 진행을 위해 환자분 성함과 연락처를 함께 알려주세요. (예: 홍길동 010-1234-5678)"
        return "예약 진행을 위해 환자분 성함을 알려주세요."

    if primary == "patient_contact":
        if action == "modify_appointment":
            return "예약 변경을 위해 환자분 연락처를 알려주세요."
        if action == "cancel_appointment":
            return "예약 취소를 위해 환자분 연락처를 알려주세요."
        if action == "check_appointment":
            return "예약 확인을 위해 환자분 연락처를 알려주세요."
        return "예약 진행을 위해 환자분 연락처를 알려주세요."

    if primary == "slot_selection":
        return "안내드린 대체 예약 시간 중 원하시는 번호를 선택해주세요."

    if primary == "customer_name":
        return "예약 진행을 위해 환자분 성함을 알려주세요."
    if primary == "birth_date":
        return "동명이인 확인을 위해 환자분 생년월일을 YYYY-MM-DD 형식으로 알려주세요."
    if primary == "department":
        return "어느 분과로 예약할까요? 현재 예약 가능한 분과는 이비인후과, 내과, 정형외과입니다."
    if primary == "date":
        _duration_hint = " (초진 40분 / 재진 30분)" if action_context == "book_appointment" else ""
        prefix = f"{department}로 예약을 도와드릴게요." if department else ""
        return f"{prefix} 원하시는 날짜와 시간을 알려주세요.{_duration_hint}".strip()
    if primary == "time":
        is_first = customer_type not in ("재진", "revisit")
        _duration_info = "초진 환자는 40분" if is_first else "재진 환자는 30분"
        prefix = f"{department}로 예약을 도와드릴게요." if department else ""
        return f"{prefix} {_duration_info} 진료 소요됩니다. 원하시는 시간을 알려주세요. (운영: 평일 09~18시, 토 09~13시)".strip()
    if primary == "appointment_target":
        if action_context == "cancel_appointment":
            return "어떤 예약을 취소할지 날짜, 시간 또는 분과를 알려주세요."
        if action_context == "modify_appointment":
            return "어떤 예약을 변경할지 날짜, 시간 또는 분과를 알려주세요."
        if action_context == "check_appointment":
            return "어떤 예약을 확인할지 날짜, 시간 또는 분과를 알려주세요."
        return "대상 예약을 확인할 수 있도록 날짜, 시간 또는 분과를 알려주세요."

    if action_context == "book_appointment":
        return "예약을 도와드리려면 원하시는 날짜, 시간, 진료과를 알려주세요."
    return "추가로 확인이 필요한 정보가 있습니다."


def build_confirmation_question(appointment: dict, now: datetime | None = None) -> str:
    summary = format_appointment_summary(appointment, now)
    return f"{summary}로 예약할까요?"


def build_appointment_options_question(
    action_context: str,
    candidate_appointments: list[dict],
    now: datetime | None = None,
) -> str:
    options = [
        f"{index}) {format_appointment_summary(appointment, now)}"
        for index, appointment in enumerate(candidate_appointments, start=1)
    ]
    option_text = ", ".join(options)
    return f"어떤 예약인지 선택해주세요. {option_text}"


def build_success_message(
    action: str,
    *,
    department: str | None = None,
    appointment: dict | None = None,
    now: datetime | None = None,
) -> str:
    if action == "book_appointment":
        summary = format_appointment_summary(appointment or {"department": department}, now)
        return f"{summary} 예약이 완료되었습니다."

    if action == "cancel_appointment":
        summary = format_appointment_summary(appointment or {"department": department}, now)
        return f"{summary} 예약 취소가 완료되었습니다."

    if action == "modify_appointment":
        summary = format_appointment_summary(appointment or {"department": department}, now)
        return f"{summary} 기준으로 예약 변경이 완료되었습니다."

    if action == "check_appointment":
        summary = format_appointment_summary(appointment or {"department": department}, now)
        return f"확인된 예약은 {summary}입니다."

    return "요청이 처리되었습니다."
