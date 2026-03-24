
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from .classifier import safety_check as classify_safety, classify_intent
from .policy import apply_policy
from .response_builder import (
    build_appointment_options_question,
    build_confirmation_question,
    build_missing_info_question,
    build_response,
    build_success_message,
)


AFFIRMATIVE_PATTERNS = [r"^네$", r"^예$", r"^넵$", r"^맞아요$", r"좋아요", r"진행", r"예약해", r"확정"]
NEGATIVE_PATTERNS = [r"^아니오$", r"^아니요$", r"^아뇨$", r"다시", r"취소할게", r"안 할래"]
VALID_ACTIONS = {
    "book_appointment",
    "modify_appointment",
    "cancel_appointment",
    "check_appointment",
    "clarify",
    "escalate",
    "reject",
}


def _load_appointments_from_disk() -> list[dict]:
    appointments_path = Path(__file__).resolve().parent.parent / "data" / "appointments.json"
    try:
        return json.loads(appointments_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def create_session(
    *,
    customer_name: str | None = None,
    customer_type: str | None = None,
    all_appointments: list[dict] | None = None,
    context: dict | None = None,
) -> dict:
    return {
        "customer_name": customer_name,
        "customer_type": customer_type,
        "context": context or {},
        "all_appointments": list(all_appointments) if all_appointments is not None else _load_appointments_from_disk(),
        "existing_appointment": None,
        "dialogue_state": {},
        "last_result": None,
    }


def _round_confidence(value: float) -> float:
    return round(max(0.35, min(0.99, value)), 2)


def _determine_classified_intent(
    *,
    result_action: str,
    classified_intent: str | None,
    safety_result: dict | None,
    intent_result: dict | None,
) -> str:
    if classified_intent in VALID_ACTIONS:
        return classified_intent

    intent_action = (intent_result or {}).get("action")
    if intent_action in VALID_ACTIONS:
        return intent_action

    if safety_result:
        if safety_result.get("unsupported_department") or safety_result.get("unsupported_doctor"):
            return "reject"
        category = safety_result.get("category")
        if category == "medical_advice":
            return "reject"
        if category == "off_topic":
            return "reject"
        if category == "complaint":
            return "escalate"
        if category == "emergency":
            return "escalate"
        if category == "classification_error":
            return result_action if result_action in VALID_ACTIONS else "clarify"

    return result_action if result_action in VALID_ACTIONS else "clarify"


def _compute_confidence(
    *,
    result_action: str,
    classified_intent: str,
    safety_result: dict | None,
    intent_result: dict | None,
    policy_result: dict | None,
    customer_type: str | None,
) -> float:
    safety = safety_result or {}
    intent = intent_result or {}
    category = safety.get("category")

    if category and category != "safe":
        score = 0.78
        if category in {"medical_advice", "off_topic", "emergency"}:
            score += 0.15
        if safety.get("unsupported_department") or safety.get("unsupported_doctor"):
            score += 0.04
        if category == "classification_error":
            score -= 0.2
        return _round_confidence(score)

    score = 0.55
    if classified_intent in VALID_ACTIONS:
        score += 0.08
    if intent.get("department"):
        score += 0.08
    if intent.get("date"):
        score += 0.07
    if intent.get("time"):
        score += 0.07
    if customer_type:
        score += 0.05

    if policy_result is not None:
        score += 0.08 if policy_result.get("allowed") else 0.05

    missing_info = intent.get("missing_info") or []
    if result_action == "clarify":
        score -= 0.15
    if len(missing_info) >= 2:
        score -= 0.05
    if intent.get("error"):
        score -= 0.1
    if classified_intent != result_action and result_action in {"clarify", "reject", "escalate"}:
        score -= 0.03

    return _round_confidence(score)


def _build_reasoning(
    *,
    result_action: str,
    classified_intent: str,
    safety_result: dict | None,
    intent_result: dict | None,
    policy_result: dict | None,
    customer_type: str | None,
) -> str:
    safety = safety_result or {}
    intent = intent_result or {}
    parts: list[str] = []

    if safety.get("unsupported_department"):
        parts.append(f"지원하지 않는 분과({safety['unsupported_department']}) 감지")
    elif safety.get("unsupported_doctor"):
        parts.append(f"확인되지 않은 의료진({safety['unsupported_doctor']}) 감지")
    else:
        category = safety.get("category")
        if category == "medical_advice":
            parts.append("의료 상담 요청 감지")
        elif category == "off_topic":
            parts.append("예약 외 요청 또는 프롬프트 인젝션 감지")
        elif category == "complaint":
            parts.append("강한 불만 또는 상담원 연결 필요 요청 감지")
        elif category == "emergency":
            parts.append("급성 통증 또는 응급 표현 감지")
        elif category == "classification_error":
            parts.append("안전성 판별 실패")
        elif category == "safe":
            parts.append("안전 게이트 통과")

    if not safety or safety.get("category") == "safe":
        if customer_type:
            parts.append(f"{customer_type} 환자")
        if classified_intent:
            parts.append(f"{classified_intent}로 분류")

        extracted = []
        if intent.get("department"):
            extracted.append(intent["department"])
        if intent.get("date"):
            extracted.append(intent["date"])
        if intent.get("time"):
            extracted.append(intent["time"])
        if extracted:
            parts.append("/".join(extracted) + " 정보 확인")

        missing_info = intent.get("missing_info") or []
        if missing_info:
            parts.append("필수 정보 부족(" + ", ".join(missing_info) + ")")

        if policy_result is not None:
            if policy_result.get("allowed"):
                parts.append("정책 위반 없음")
            else:
                parts.append(policy_result.get("reason", "정책 재확인 필요"))

    if result_action in {"reject", "escalate"} and safety.get("category") not in {None, "safe"}:
        parts.append(f"safety gate에서 {result_action}")
    elif result_action == "clarify":
        parts.append("추가 확인 필요")

    ordered_parts: list[str] = []
    for part in parts:
        if part and part not in ordered_parts:
            ordered_parts.append(part)
    return ", ".join(ordered_parts) if ordered_parts else "판단 근거를 추가 확인 중입니다"


def _build_runtime_fields(
    *,
    ticket: dict | None,
    result_action: str,
    department: str | None,
    classified_intent: str | None,
    safety_result: dict | None,
    intent_result: dict | None,
    policy_result: dict | None,
    customer_type: str | None,
) -> dict:
    resolved_classified_intent = _determine_classified_intent(
        result_action=result_action,
        classified_intent=classified_intent,
        safety_result=safety_result,
        intent_result=intent_result,
    )
    return {
        "ticket_id": (ticket or {}).get("ticket_id"),
        "classified_intent": resolved_classified_intent,
        "department": department,
        "confidence": _compute_confidence(
            result_action=result_action,
            classified_intent=resolved_classified_intent,
            safety_result=safety_result,
            intent_result=intent_result,
            policy_result=policy_result,
            customer_type=customer_type,
        ),
        "reasoning": _build_reasoning(
            result_action=result_action,
            classified_intent=resolved_classified_intent,
            safety_result=safety_result,
            intent_result=intent_result,
            policy_result=policy_result,
            customer_type=customer_type,
        ),
    }


def _resolve_existing_appointment_from_ticket(
    ticket: dict,
    all_appointments: list[dict],
    existing_appointment: dict | None,
    now: datetime,
) -> dict | None:
    if existing_appointment is not None:
        return existing_appointment

    context = ticket.get("context") or {}
    if not context.get("has_existing_appointment"):
        return None

    candidates = _find_customer_appointments(ticket, all_appointments, None)
    if not candidates:
        return None

    requested_department = context.get("existing_appointment_department")
    requested_date = context.get("existing_appointment_date")

    filtered = []
    for appointment in candidates:
        slots = _extract_candidate_slots(appointment, now)
        if requested_department and appointment.get("department") != requested_department:
            continue
        if requested_date and slots.get("date") != requested_date:
            continue
        filtered.append(appointment)

    if len(filtered) == 1:
        return filtered[0]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _normalize_text(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _init_session_state(session_state: dict | None) -> dict:
    if session_state is None:
        return {
            "conversation_history": [],
            "accumulated_slots": {"date": None, "time": None, "department": None},
            "pending_confirmation": None,
            "pending_action": None,
            "pending_missing_info": [],
            "pending_candidates": None,
        }

    session_state.setdefault("conversation_history", [])
    session_state.setdefault("accumulated_slots", {"date": None, "time": None, "department": None})
    session_state.setdefault("pending_confirmation", None)
    session_state.setdefault("pending_action", None)
    session_state.setdefault("pending_missing_info", [])
    session_state.setdefault("pending_candidates", None)
    return session_state


def _record_history(session_state: dict | None, role: str, content: str) -> None:
    if session_state is None:
        return
    session_state["conversation_history"].append({"role": role, "content": content})


def _clear_dialogue_state(session_state: dict | None) -> None:
    if session_state is None:
        return
    session_state["accumulated_slots"] = {"date": None, "time": None, "department": None}
    session_state["pending_confirmation"] = None
    session_state["pending_action"] = None
    session_state["pending_missing_info"] = []
    session_state["pending_candidates"] = None


def _build_response_and_record(session_state: dict | None, **kwargs) -> dict:
    ticket = kwargs.pop("ticket", None)
    classified_intent = kwargs.pop("classified_intent", None)
    safety_result = kwargs.pop("safety_result", None)
    intent_result = kwargs.pop("intent_result", None)
    policy_result = kwargs.pop("policy_result", None)
    customer_type = kwargs.pop("customer_type", None)

    result = build_response(**kwargs)
    result.update(
        _build_runtime_fields(
            ticket=ticket,
            result_action=result.get("action"),
            department=result.get("department"),
            classified_intent=classified_intent,
            safety_result=safety_result,
            intent_result=intent_result,
            policy_result=policy_result,
            customer_type=customer_type,
        )
    )
    _record_history(session_state, "assistant", result.get("response", ""))
    if session_state is not None:
        session_state["last_result"] = result
    return result


def _is_affirmative(message: str) -> bool:
    text = _normalize_text(message)
    return any(re.search(pattern, text) for pattern in AFFIRMATIVE_PATTERNS)


def _is_negative(message: str) -> bool:
    text = _normalize_text(message)
    return any(re.search(pattern, text) for pattern in NEGATIVE_PATTERNS)


def _infer_requested_action(message: str) -> str | None:
    text = _normalize_text(message)
    if any(keyword in text for keyword in ["취소", "예약 취소"]):
        return "cancel_appointment"
    if any(keyword in text for keyword in ["변경", "바꿔", "옮겨", "수정"]):
        return "modify_appointment"
    if any(keyword in text for keyword in ["확인", "조회"]):
        return "check_appointment"
    if any(keyword in text for keyword in ["예약", "진료", "접수"]):
        return "book_appointment"
    return None


def _classify_intent_with_optional_now(user_message: str, now: datetime) -> dict:
    if isinstance(classify_intent, Mock):
        return classify_intent(user_message)
    try:
        return classify_intent(user_message, now=now)
    except TypeError:
        return classify_intent(user_message)


def _build_safety_response(
    safety_result: dict,
    session_state: dict | None = None,
    *,
    ticket: dict | None = None,
    customer_type: str | None = None,
) -> dict:
    category = safety_result.get("category")
    fallback_action = safety_result.get("fallback_action")
    fallback_message = safety_result.get("fallback_message")

    if category == "emergency":
        return _build_response_and_record(
            session_state,
            action="escalate",
            message="급성 통증 또는 응급 가능성이 있어 자동 예약 대신 상담원 또는 의료진 확인이 먼저 필요합니다.",
            ticket=ticket,
            safety_result=safety_result,
            customer_type=customer_type,
        )

    if category == "medical_advice":
        return _build_response_and_record(
            session_state,
            action="reject",
            message="의료법상 의료 상담(진단, 약물, 치료 방법 안내)은 도와드릴 수 없습니다. 원하시면 진료 예약을 도와드릴게요.",
            ticket=ticket,
            safety_result=safety_result,
            customer_type=customer_type,
        )

    if category == "off_topic":
        return _build_response_and_record(
            session_state,
            action="reject",
            message="코비메디 예약 관련 문의만 도와드릴 수 있습니다.",
            ticket=ticket,
            safety_result=safety_result,
            customer_type=customer_type,
        )

    if category == "complaint":
        return _build_response_and_record(
            session_state,
            action="escalate",
            message="불편을 드려 죄송합니다. 해당 요청은 상담원이 이어서 도와드릴 수 있도록 연결해 드리겠습니다.",
            ticket=ticket,
            safety_result=safety_result,
            customer_type=customer_type,
        )

    if category == "classification_error" and fallback_action == "clarify":
        return _build_response_and_record(
            session_state,
            action="clarify",
            message=fallback_message or "일시적 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
            ticket=ticket,
            safety_result=safety_result,
            customer_type=customer_type,
        )

    return _build_response_and_record(
        session_state,
        action="reject",
        message="안전성 판단에 실패했습니다. 예약 관련 문의를 다시 작성해 주세요.",
        ticket=ticket,
        safety_result=safety_result,
        customer_type=customer_type,
    )


def _build_unknown_entity_response(
    safety_result: dict,
    session_state: dict | None = None,
    *,
    ticket: dict | None = None,
    customer_type: str | None = None,
) -> dict | None:
    unsupported_department = safety_result.get("unsupported_department")
    unsupported_doctor = safety_result.get("unsupported_doctor")

    if unsupported_department:
        return _build_response_and_record(
            session_state,
            action="reject",
            message=f"코비메디에는 {unsupported_department}가 없습니다. 현재 예약 가능한 분과는 이비인후과, 내과, 정형외과입니다.",
            ticket=ticket,
            safety_result=safety_result,
            customer_type=customer_type,
        )

    if unsupported_doctor:
        return _build_response_and_record(
            session_state,
            action="reject",
            message=f"{unsupported_doctor}은(는) 코비메디에서 확인되지 않습니다. 현재 확인 가능한 의료진은 이춘영 원장, 김만수 원장, 원징수 원장입니다.",
            ticket=ticket,
            safety_result=safety_result,
            customer_type=customer_type,
        )

    return None


def _build_department_guidance_response(
    department: str | None,
    session_state: dict | None = None,
    *,
    ticket: dict | None = None,
    safety_result: dict | None = None,
    customer_type: str | None = None,
) -> dict:
    if department:
        return _build_response_and_record(
            session_state,
            action="clarify",
            message=f"증상만으로 진단이나 치료 방법을 안내할 수는 없지만, 예약 안내 기준으로는 {department} 진료가 적절할 수 있습니다. 원하시는 날짜와 시간을 알려주시면 {department} 예약을 도와드릴게요.",
            department=department,
            ticket=ticket,
            safety_result=safety_result,
            intent_result={"action": "clarify", "department": department},
            customer_type=customer_type,
        )

    return _build_response_and_record(
        session_state,
        action="clarify",
        message="증상만으로 진단은 도와드릴 수 없습니다. 예약을 원하시면 원하시는 날짜, 시간, 진료과를 알려주세요.",
        ticket=ticket,
        safety_result=safety_result,
        intent_result={"action": "clarify", "department": None},
        customer_type=customer_type,
    )


def _normalize_safety_result(safety_output) -> dict:
    if isinstance(safety_output, dict):
        return safety_output

    category = safety_output or "classification_error"
    return {
        "category": category,
        "department_hint": None,
        "mixed_department_guidance": False,
        "unsupported_department": None,
        "unsupported_doctor": None,
    }


def _merge_accumulated_slots(session_state: dict | None, intent_result: dict) -> dict:
    accumulated = (session_state or {}).get("accumulated_slots", {})
    merged = {
        "department": intent_result.get("department") or accumulated.get("department"),
        "date": intent_result.get("date") or accumulated.get("date"),
        "time": intent_result.get("time") or accumulated.get("time"),
    }
    if session_state is not None:
        session_state["accumulated_slots"] = merged.copy()
    return merged


def _determine_missing_info(action: str, slots: dict) -> list[str]:
    if action != "book_appointment":
        return []

    missing = []
    if not slots.get("department"):
        missing.append("department")
    if not slots.get("date"):
        missing.append("date")
    if not slots.get("time"):
        missing.append("time")
    return missing


def _build_booking_time(date_value: str | None, time_value: str | None, now: datetime) -> str | None:
    if not date_value or not time_value:
        return None
    try:
        booking_dt = datetime.fromisoformat(f"{date_value}T{time_value}:00")
    except ValueError:
        return None

    reference_tz = now.tzinfo or timezone.utc
    if booking_dt.tzinfo is None:
        booking_dt = booking_dt.replace(tzinfo=reference_tz)
    return booking_dt.isoformat()


def _extract_candidate_slots(appointment: dict, now: datetime) -> dict:
    booking_time = appointment.get("booking_time")
    appointment_dt = None
    if booking_time:
        raw_value = str(booking_time)
        if raw_value.endswith("Z"):
            raw_value = f"{raw_value[:-1]}+00:00"
        try:
            appointment_dt = datetime.fromisoformat(raw_value)
        except ValueError:
            appointment_dt = None

    if appointment_dt and appointment_dt.tzinfo is None:
        appointment_dt = appointment_dt.replace(tzinfo=now.tzinfo or timezone.utc)

    return {
        "department": appointment.get("department"),
        "date": appointment_dt.date().isoformat() if appointment_dt else appointment.get("date"),
        "time": appointment_dt.strftime("%H:%M") if appointment_dt else appointment.get("time"),
    }


def _find_customer_appointments(ticket: dict, all_appointments: list[dict], existing_appointment: dict | None) -> list[dict]:
    customer_name = ticket.get("customer_name")
    if customer_name:
        matches = [appointment for appointment in all_appointments if appointment.get("customer_name") == customer_name]
        if matches:
            return matches
    if existing_appointment:
        return [existing_appointment]
    return []


def _filter_candidate_appointments(candidates: list[dict], slots: dict, now: datetime) -> list[dict]:
    filtered = []
    for appointment in candidates:
        appointment_slots = _extract_candidate_slots(appointment, now)
        if slots.get("department") and appointment_slots.get("department") != slots.get("department"):
            continue
        if slots.get("date") and appointment_slots.get("date") != slots.get("date"):
            continue
        if slots.get("time") and appointment_slots.get("time") != slots.get("time"):
            continue
        filtered.append(appointment)
    return filtered


def _resolve_candidate_selection(message: str, candidates: list[dict], now: datetime) -> dict | None:
    text = _normalize_text(message)
    number_match = re.search(r"(\d+)", text)
    if number_match:
        index = int(number_match.group(1)) - 1
        if 0 <= index < len(candidates):
            return candidates[index]

    for candidate in candidates:
        slots = _extract_candidate_slots(candidate, now)
        if slots.get("department") and slots["department"] in text:
            return candidate
        if slots.get("date") and slots["date"] in text:
            return candidate
        if slots.get("time") and slots["time"] in text:
            return candidate
    return None


def _handle_pending_confirmation(
    user_message: str,
    session_state: dict,
    now: datetime,
    *,
    ticket: dict | None = None,
    customer_type: str | None = None,
) -> dict | None:
    pending_confirmation = session_state.get("pending_confirmation")
    if not pending_confirmation:
        return None

    if _is_affirmative(user_message):
        appointment = pending_confirmation.get("appointment", {})
        action = pending_confirmation.get("action", "book_appointment")
        message = build_success_message(action, department=appointment.get("department"), appointment=appointment, now=now)
        _clear_dialogue_state(session_state)
        return _build_response_and_record(
            session_state,
            action=action,
            message=message,
            department=appointment.get("department"),
            ticket=ticket,
            classified_intent=action,
            intent_result={"action": action, "department": appointment.get("department")},
            customer_type=customer_type or appointment.get("customer_type"),
        )

    if _is_negative(user_message):
        session_state["pending_confirmation"] = None
        return _build_response_and_record(
            session_state,
            action="clarify",
            message="알겠습니다. 원하시는 다른 날짜, 시간 또는 분과를 알려주세요.",
            department=session_state.get("accumulated_slots", {}).get("department"),
            ticket=ticket,
            classified_intent="clarify",
            intent_result={
                "action": "clarify",
                "department": session_state.get("accumulated_slots", {}).get("department"),
            },
            customer_type=customer_type,
        )

    session_state["pending_confirmation"] = None
    return None


def process_ticket(
    ticket: dict,
    all_appointments: list = None,
    existing_appointment: dict = None,
    session_state: dict | None = None,
    now: datetime = None,
) -> dict:
    """Process a ticket through safety, classification, policy, and optional chat session state."""
    if now is None:
        now = datetime.now(timezone.utc)
    if all_appointments is None:
        all_appointments = _load_appointments_from_disk()

    customer_type = ticket.get("customer_type")
    existing_appointment = _resolve_existing_appointment_from_ticket(
        ticket,
        all_appointments,
        existing_appointment,
        now,
    )

    state = _init_session_state(session_state) if session_state is not None else None
    user_message = ticket.get("message")
    if not user_message:
        return _build_response_and_record(
            state,
            action="reject",
            message="문의 내용이 없습니다.",
            ticket=ticket,
            classified_intent="reject",
            customer_type=customer_type,
        )

    _record_history(state, "user", user_message)

    if state is not None:
        pending_confirmation_result = _handle_pending_confirmation(
            user_message,
            state,
            now,
            ticket=ticket,
            customer_type=customer_type,
        )
        if pending_confirmation_result is not None:
            return pending_confirmation_result

    selected_existing_appointment = None
    action_override = None
    if state is not None and state.get("pending_candidates"):
        pending_action = state.get("pending_action") or "check_appointment"
        selected_existing_appointment = _resolve_candidate_selection(user_message, state["pending_candidates"], now)
        if selected_existing_appointment is None:
            message = build_appointment_options_question(pending_action, state["pending_candidates"], now)
            return _build_response_and_record(
                state,
                action="clarify",
                message=message,
                ticket=ticket,
                classified_intent=pending_action,
                intent_result={"action": pending_action, "missing_info": ["appointment_target"]},
                customer_type=customer_type,
            )
        action_override = pending_action
        state["pending_candidates"] = None
        state["accumulated_slots"] = _extract_candidate_slots(selected_existing_appointment, now)

    if selected_existing_appointment is None:
        safety_result = _normalize_safety_result(classify_safety(user_message))

        if safety_result.get("category") != "safe":
            return _build_safety_response(
                safety_result,
                state,
                ticket=ticket,
                customer_type=customer_type,
            )

        unknown_entity_response = _build_unknown_entity_response(
            safety_result,
            state,
            ticket=ticket,
            customer_type=customer_type,
        )
        if unknown_entity_response is not None:
            return unknown_entity_response

        if safety_result.get("mixed_department_guidance"):
            return _build_department_guidance_response(
                safety_result.get("department_hint"),
                state,
                ticket=ticket,
                safety_result=safety_result,
                customer_type=customer_type,
            )

        intent_result = _classify_intent_with_optional_now(user_message, now)
        if intent_result.get("error"):
            fallback_action = intent_result.get("fallback_action")
            fallback_message = intent_result.get("fallback_message")
            return _build_response_and_record(
                state,
                action=fallback_action if fallback_action in VALID_ACTIONS else "clarify",
                message=fallback_message or "의도를 파악하는 중 오류가 발생했습니다. 다시 시도해 주세요.",
                ticket=ticket,
                classified_intent=fallback_action if fallback_action in VALID_ACTIONS else "clarify",
                safety_result=safety_result,
                intent_result=intent_result,
                customer_type=customer_type,
            )
    else:
        safety_result = {"category": "safe", "department_hint": None}
        slots = _extract_candidate_slots(selected_existing_appointment, now)
        intent_result = {
            "action": action_override,
            "department": slots.get("department"),
            "date": slots.get("date"),
            "time": slots.get("time"),
            "missing_info": [],
        }

    action = intent_result.get("action")
    inferred_action = _infer_requested_action(user_message)
    if action == "clarify":
        pending_action = (state or {}).get("pending_action")
        has_booking_slots = any(intent_result.get(key) for key in ["department", "date", "time"])
        if pending_action:
            action = pending_action
        elif inferred_action in {"cancel_appointment", "modify_appointment", "check_appointment"}:
            action = inferred_action
        elif inferred_action == "book_appointment" and has_booking_slots:
            action = inferred_action

    merged_slots = _merge_accumulated_slots(state, intent_result)
    department = merged_slots.get("department") or intent_result.get("department") or safety_result.get("department_hint")
    classified_intent = intent_result.get("action")

    if state is not None:
        state["pending_action"] = action

    if action == "book_appointment":
        missing_info = _determine_missing_info(action, merged_slots)
        if missing_info:
            if state is not None:
                state["pending_missing_info"] = missing_info
            question = build_missing_info_question(missing_info, department=department, action_context=action)
            return _build_response_and_record(
                state,
                action="clarify",
                message=question,
                department=department,
                ticket=ticket,
                classified_intent=classified_intent,
                safety_result=safety_result,
                intent_result={**intent_result, "action": "clarify", "department": department, "missing_info": missing_info},
                customer_type=customer_type,
            )

    if action == "clarify":
        if department:
            message = f"{department} 예약을 도와드릴 수 있습니다. 원하시는 날짜와 시간을 알려주시겠어요?"
        else:
            message = "예약 관련하여 더 자세한 정보가 필요합니다. 원하시는 날짜, 시간, 진료과를 알려주시겠어요?"
        return _build_response_and_record(
            state,
            action="clarify",
            message=message,
            department=department,
            ticket=ticket,
            classified_intent=classified_intent,
            safety_result=safety_result,
            intent_result={**intent_result, "department": department},
            customer_type=customer_type,
        )

    customer_type = customer_type or "재진"
    booking_time = _build_booking_time(merged_slots.get("date"), merged_slots.get("time"), now)
    target_existing_appointment = selected_existing_appointment or existing_appointment

    if action in {"modify_appointment", "cancel_appointment", "check_appointment"} and selected_existing_appointment is None:
        customer_appointments = _find_customer_appointments(ticket, all_appointments, existing_appointment)
        filtered_candidates = _filter_candidate_appointments(customer_appointments, merged_slots, now)
        candidates = filtered_candidates or customer_appointments
        if len(candidates) > 1:
            if state is not None:
                state["pending_candidates"] = candidates
                state["pending_action"] = action
            message = build_appointment_options_question(action, candidates, now)
            return _build_response_and_record(
                state,
                action="clarify",
                message=message,
                department=department,
                ticket=ticket,
                classified_intent=classified_intent,
                safety_result=safety_result,
                intent_result={**intent_result, "action": action, "department": department},
                customer_type=customer_type,
            )
        if len(candidates) == 1:
            target_existing_appointment = candidates[0]

    intent_payload = {
        **intent_result,
        "action": action,
        "department": department,
        "date": merged_slots.get("date"),
        "time": merged_slots.get("time"),
        "booking_time": booking_time or ticket.get("booking_time"),
        "customer_type": customer_type,
    }

    policy_result = apply_policy(intent_payload, target_existing_appointment, all_appointments, now)

    if not policy_result["allowed"]:
        recommended_action = policy_result.get("recommended_action")
        message = policy_result["reason"]
        alternatives = policy_result.get("alternative_slots") or []
        if alternatives:
            message = f"{message} 가능한 다른 시간은 {', '.join(alternatives)} 입니다."

        if recommended_action == "clarify":
            return _build_response_and_record(
                state,
                action="clarify",
                message=message,
                department=department,
                ticket=ticket,
                classified_intent=classified_intent,
                safety_result=safety_result,
                intent_result=intent_payload,
                policy_result=policy_result,
                customer_type=customer_type,
            )

        if recommended_action == "escalate":
            return _build_response_and_record(
                state,
                action="escalate",
                message=message,
                department=department,
                ticket=ticket,
                classified_intent=classified_intent,
                safety_result=safety_result,
                intent_result=intent_payload,
                policy_result=policy_result,
                customer_type=customer_type,
            )

        return _build_response_and_record(
            state,
            action="reject",
            message=message,
            department=department,
            ticket=ticket,
            classified_intent=classified_intent,
            safety_result=safety_result,
            intent_result=intent_payload,
            policy_result=policy_result,
            customer_type=customer_type,
        )

    if action == "book_appointment" and state is not None:
        appointment = {
            "customer_name": ticket.get("customer_name"),
            "department": department,
            "date": merged_slots.get("date"),
            "time": merged_slots.get("time"),
            "booking_time": booking_time,
            "customer_type": customer_type,
        }
        state["pending_confirmation"] = {"action": "book_appointment", "appointment": appointment}
        message = build_confirmation_question(appointment, now)
        return _build_response_and_record(
            state,
            action="clarify",
            message=message,
            department=department,
            ticket=ticket,
            classified_intent=classified_intent,
            safety_result=safety_result,
            intent_result=intent_payload,
            policy_result=policy_result,
            customer_type=customer_type,
        )

    success_reference = target_existing_appointment or {
        "department": department,
        "date": merged_slots.get("date"),
        "time": merged_slots.get("time"),
        "booking_time": booking_time,
    }
    message = build_success_message(action, department=department, appointment=success_reference, now=now)
    _clear_dialogue_state(state)
    return _build_response_and_record(
        state,
        action=action,
        message=message,
        department=department,
        ticket=ticket,
        classified_intent=classified_intent,
        safety_result=safety_result,
        intent_result=intent_payload,
        policy_result=policy_result,
        customer_type=customer_type,
    )


def process_message(user_message: str, session: dict | None = None, now: datetime = None) -> dict:
    if now is None:
        now = datetime.now(timezone.utc)

    if session is None:
        session = create_session()

    dialogue_state = session.setdefault("dialogue_state", {})
    all_appointments = session.get("all_appointments")
    if all_appointments is None:
        all_appointments = _load_appointments_from_disk()
        session["all_appointments"] = all_appointments

    ticket = {
        "message": user_message,
        "customer_name": session.get("customer_name"),
        "customer_type": session.get("customer_type"),
        "context": session.get("context", {}),
    }

    existing_appointment = _resolve_existing_appointment_from_ticket(
        ticket,
        all_appointments,
        session.get("existing_appointment"),
        now,
    )
    session["existing_appointment"] = existing_appointment

    result = process_ticket(
        ticket=ticket,
        all_appointments=all_appointments,
        existing_appointment=existing_appointment,
        session_state=dialogue_state,
        now=now,
    )
    session["last_result"] = result
    return result
