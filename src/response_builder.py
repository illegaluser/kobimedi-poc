"""
src/response_builder.py — 사용자 응답 메시지 생성 모듈

agent.py가 최종 응답을 구성할 때 호출하는 유틸리티 함수 모음.
날짜/시간을 자연어로 포맷팅하고, 누락 정보 질문·확인 질문·성공 메시지 등
챗봇이 출력하는 모든 한국어 문구를 이 모듈에서 생성한다.

핵심 원칙:
  - 의료 관련 자유 텍스트를 생성하지 않는다 (하드코딩된 문구만 사용).
  - LLM을 호출하지 않는다 — 모든 문구가 결정론적이다.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .classifier import SUPPORTED_DOCTORS


# 의사 이름 → 분과 매핑 (예: "김만수" → "내과")
DOCTOR_DEPARTMENT_MAP = SUPPORTED_DOCTORS
# 역매핑: 분과 → 의사 이름 (예: "내과" → "김만수")
DEPARTMENT_DOCTOR_MAP = {department: doctor for doctor, department in DOCTOR_DEPARTMENT_MAP.items()}


def build_response(action: str, message: str, **kwargs) -> dict:
    """agent.py가 반환하는 최종 응답 딕셔너리를 조립한다.
    기본 구조: {"action": "...", "response": "..."}
    추가 키워드 인자(department, confidence 등)는 그대로 병합된다."""
    response = {
        "action": action,
        "response": message,
    }
    response.update(kwargs)
    return response


def _ensure_datetime(value: str | datetime | None, now: datetime | None = None) -> datetime | None:
    """문자열 또는 datetime을 timezone-aware datetime으로 정규화한다.
    ISO 형식 문자열("2026-04-07T14:00:00+09:00")을 파싱하며,
    timezone 정보가 없으면 now의 timezone 또는 UTC를 부여한다.
    파싱 실패 시 None을 반환한다 (오류를 삼키지 않고 None으로 표현)."""
    if value is None:
        return None

    if isinstance(value, datetime):
        dt = value
    else:
        raw_value = str(value).strip()
        if not raw_value:
            return None
        # ISO 형식의 "Z" 접미사를 "+00:00"으로 변환 (Python fromisoformat 호환)
        if raw_value.endswith("Z"):
            raw_value = f"{raw_value[:-1]}+00:00"
        try:
            dt = datetime.fromisoformat(raw_value)
        except ValueError:
            return None

    # timezone이 없으면 참조 시각의 timezone 또는 UTC를 부여
    reference_tz = now.tzinfo if now and now.tzinfo else timezone.utc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=reference_tz)
    return dt


def _format_date_phrase(dt: datetime | None, now: datetime | None = None) -> str:
    """날짜를 한국어 자연어로 변환한다.
    오늘이면 "오늘(4/7)", 내일이면 "내일(4/8)", 그 외는 "4/10" 형식."""
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
    """시간을 한국어 자연어로 변환한다. 예: 14:00 → "오후 2시", 09:30 → "오전 9시 30분"."""
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
    """예약 정보를 한 줄 요약 문자열로 변환한다.
    예: "4/14 오전 10시 내과 김만수 진료"
    날짜/시간/분과/의사를 가용한 정보 범위 내에서 조합한다."""
    department = appointment.get("department")
    doctor_name = appointment.get("doctor_name") or DEPARTMENT_DOCTOR_MAP.get(department)
    booking_time = appointment.get("booking_time")

    # booking_time이 없으면 date + time 필드로 조립 시도
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
    """누락된 정보(missing_fields)에 따라 사용자에게 던질 추가 질문을 생성한다.

    missing_fields의 첫 번째 항목(가장 우선순위가 높은 누락 필드)에 따라 분기:
      - is_proxy_booking : "본인이신가요?" (환자 식별 — 최우선)
      - patient_name     : "성함을 알려주세요" (연락처도 함께 누락이면 동시 요청)
      - patient_contact  : "연락처를 알려주세요"
      - department       : "어느 분과로 예약할까요?"
      - date / time      : "원하시는 날짜와 시간을 알려주세요"
      - appointment_target: "어떤 예약인지 알려주세요" (변경/취소 대상 특정)

    action_context에 따라 문구가 미세하게 달라진다 (예약/변경/취소/조회)."""
    missing_fields = missing_fields or []
    primary = missing_fields[0] if missing_fields else None

    action = action_context or ""

    # ── 1순위: 본인/대리 확인 (환자 식별) ──
    if primary == "is_proxy_booking":
        if action == "modify_appointment":
            return "예약 변경을 요청하시는 분이 환자 본인이신가요, 아니면 가족이나 지인을 대신하여 요청하시는 건가요?"
        if action == "cancel_appointment":
            return "예약 취소를 요청하시는 분이 환자 본인이신가요, 아니면 가족이나 지인을 대신하여 요청하시는 건가요?"
        if action == "check_appointment":
            return "예약 확인을 요청하시는 분이 환자 본인이신가요, 아니면 가족이나 지인을 대신하여 요청하시는 건가요?"
        return "예약하시는 분이 환자 본인이신가요, 아니면 가족이나 지인을 대신하여 예약하시는 건가요?"

    # ── 2순위: 환자 성함 ──
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
        if has_contact_missing:
            return "예약 진행을 위해 환자분 성함과 연락처를 함께 알려주세요. (예: 홍길동 010-1234-5678)"
        return "예약 진행을 위해 환자분 성함을 알려주세요."

    # ── 3순위: 환자 연락처 ──
    if primary == "patient_contact":
        if action == "modify_appointment":
            return "예약 변경을 위해 환자분 연락처를 알려주세요."
        if action == "cancel_appointment":
            return "예약 취소를 위해 환자분 연락처를 알려주세요."
        if action == "check_appointment":
            return "예약 확인을 위해 환자분 연락처를 알려주세요."
        return "예약 진행을 위해 환자분 연락처를 알려주세요."

    # ── 대안 슬롯 선택 ──
    if primary == "slot_selection":
        return "안내드린 대체 예약 시간 중 원하시는 번호를 선택해주세요."

    # ── 기타 누락 필드별 질문 ──
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

    # ── 폴백 ──
    if action_context == "book_appointment":
        return "예약을 도와드리려면 원하시는 날짜, 시간, 진료과를 알려주세요."
    return "추가로 확인이 필요한 정보가 있습니다."


def build_confirmation_question(appointment: dict, now: datetime | None = None) -> str:
    """예약 확정 직전, 사용자에게 최종 확인 질문을 생성한다.
    예: "4/14 오전 10시 내과 김만수 진료로 예약할까요?" """
    summary = format_appointment_summary(appointment, now)
    return f"{summary}로 예약할까요?"


def build_appointment_options_question(
    action_context: str,
    candidate_appointments: list[dict],
    now: datetime | None = None,
) -> str:
    """동일 환자의 예약이 여러 건일 때, 번호 선택지를 생성한다.
    예: "어떤 예약인지 선택해주세요. 1) 4/14 오전 10시 내과, 2) 4/15 오후 2시 정형외과" """
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
    """action별 성공 완료 메시지를 생성한다.
      - book_appointment   → "... 예약이 완료되었습니다."
      - cancel_appointment → "... 예약 취소가 완료되었습니다."
      - modify_appointment → "... 기준으로 예약 변경이 완료되었습니다."
      - check_appointment  → "확인된 예약은 ...입니다."
    """
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
