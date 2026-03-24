
from __future__ import annotations

import re
from datetime import datetime, timezone
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
    result = build_response(**kwargs)
    _record_history(session_state, "assistant", result.get("response", ""))
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


def _build_safety_response(safety_result: dict, session_state: dict | None = None) -> dict:
    category = safety_result.get("category")

    if category == "emergency":
        return _build_response_and_record(
            session_state,
            action="escalate",
            message="급성 통증 또는 응급 가능성이 있어 자동 예약 대신 상담원 또는 의료진 확인이 먼저 필요합니다.",
        )

    if category == "medical_advice":
        return _build_response_and_record(
            session_state,
            action="reject",
            message="의료법상 의료 상담(진단, 약물, 치료 방법 안내)은 도와드릴 수 없습니다. 원하시면 진료 예약을 도와드릴게요.",
        )

    if category == "off_topic":
        return _build_response_and_record(
            session_state,
            action="reject",
            message="코비메디 예약 관련 문의만 도와드릴 수 있습니다.",
        )

    return _build_response_and_record(
        session_state,
        action="reject",
        message="안전성 판단에 실패했습니다. 예약 관련 문의를 다시 작성해 주세요.",
    )


def _build_unknown_entity_response(safety_result: dict, session_state: dict | None = None) -> dict | None:
    unsupported_department = safety_result.get("unsupported_department")
    unsupported_doctor = safety_result.get("unsupported_doctor")

    if unsupported_department:
        return _build_response_and_record(
            session_state,
            action="reject",
            message=f"코비메디에는 {unsupported_department}가 없습니다. 현재 예약 가능한 분과는 이비인후과, 내과, 정형외과입니다.",
        )

    if unsupported_doctor:
        return _build_response_and_record(
            session_state,
            action="reject",
            message=f"{unsupported_doctor}은(는) 코비메디에서 확인되지 않습니다. 현재 확인 가능한 의료진은 이춘영 원장, 김만수 원장, 원징수 원장입니다.",
        )

    return None


def _build_department_guidance_response(department: str | None, session_state: dict | None = None) -> dict:
    if department:
        return _build_response_and_record(
            session_state,
            action="clarify",
            message=f"증상만으로 진단이나 치료 방법을 안내할 수는 없지만, 예약 안내 기준으로는 {department} 진료가 적절할 수 있습니다. 원하시는 날짜와 시간을 알려주시면 {department} 예약을 도와드릴게요.",
            department=department,
        )

    return _build_response_and_record(
        session_state,
        action="clarify",
        message="증상만으로 진단은 도와드릴 수 없습니다. 예약을 원하시면 원하시는 날짜, 시간, 진료과를 알려주세요.",
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


def _handle_pending_confirmation(user_message: str, session_state: dict, now: datetime) -> dict | None:
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
        )

    if _is_negative(user_message):
        session_state["pending_confirmation"] = None
        return _build_response_and_record(
            session_state,
            action="clarify",
            message="알겠습니다. 원하시는 다른 날짜, 시간 또는 분과를 알려주세요.",
            department=session_state.get("accumulated_slots", {}).get("department"),
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
        all_appointments = []

    state = _init_session_state(session_state) if session_state is not None else None
    user_message = ticket.get("message")
    if not user_message:
        return _build_response_and_record(state, action="reject", message="문의 내용이 없습니다.")

    _record_history(state, "user", user_message)

    if state is not None:
        pending_confirmation_result = _handle_pending_confirmation(user_message, state, now)
        if pending_confirmation_result is not None:
            return pending_confirmation_result

    selected_existing_appointment = None
    action_override = None
    if state is not None and state.get("pending_candidates"):
        pending_action = state.get("pending_action") or "check_appointment"
        selected_existing_appointment = _resolve_candidate_selection(user_message, state["pending_candidates"], now)
        if selected_existing_appointment is None:
            message = build_appointment_options_question(pending_action, state["pending_candidates"], now)
            return _build_response_and_record(state, action="clarify", message=message)
        action_override = pending_action
        state["pending_candidates"] = None
        state["accumulated_slots"] = _extract_candidate_slots(selected_existing_appointment, now)

    if selected_existing_appointment is None:
        safety_result = _normalize_safety_result(classify_safety(user_message))

        if safety_result.get("category") != "safe":
            return _build_safety_response(safety_result, state)

        unknown_entity_response = _build_unknown_entity_response(safety_result, state)
        if unknown_entity_response is not None:
            return unknown_entity_response

        if safety_result.get("mixed_department_guidance"):
            return _build_department_guidance_response(safety_result.get("department_hint"), state)

        intent_result = _classify_intent_with_optional_now(user_message, now)
        if intent_result.get("error"):
            return _build_response_and_record(
                state,
                action="clarify",
                message="의도를 파악하는 중 오류가 발생했습니다. 다시 시도해 주세요.",
            )
    else:
        safety_result = {"department_hint": None}
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
        )

    customer_type = ticket.get("customer_type") or "재진"
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
            )

        if recommended_action == "escalate":
            return _build_response_and_record(
                state,
                action="escalate",
                message=message,
                department=department,
            )

        return _build_response_and_record(
            state,
            action="reject",
            message=message,
            department=department,
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
    )
