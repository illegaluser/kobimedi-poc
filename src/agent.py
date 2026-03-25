
from __future__ import annotations

import re
from datetime import datetime, timezone
from unittest.mock import Mock

from .classifier import safety_check, classify_intent
from .policy import apply_policy
from .response_builder import (
    build_appointment_options_question,
    build_confirmation_question,
    build_missing_info_question,
    build_response,
    build_success_message,
)
from .storage import (
    create_booking,
    find_bookings,
    load_bookings,
    normalize_birth_date,
    normalize_patient_contact,
    resolve_customer_type_from_history,
)
from .metrics import KpiEvent, record_kpi_event
from .models import User, Ticket as PolicyTicket


AFFIRMATIVE_PATTERNS = [r"^네$", r"^예$", r"^넵$", r"^맞아요$", r"좋아요", r"진행", r"예약해", r"확정"]
NEGATIVE_PATTERNS = [r"^아니오$", r"^아니요$", r"^아뇨$", r"다시", r"취소할게", r"안 할래", r"싫어", r"싫습니다", r"^안 ?해$", r"^안 ?할래$", r"^됐어$", r"^괜찮아요$"]
VALID_ACTIONS = {
    "book_appointment",
    "modify_appointment",
    "cancel_appointment",
    "check_appointment",
    "clarify",
    "escalate",
    "reject",
}
BOOKING_RELATED_ACTIONS = {
    "book_appointment",
    "modify_appointment",
    "cancel_appointment",
    "check_appointment",
}
PROXY_TRUE_PATTERNS = [r"대리", r"대신", r"가족", r"엄마", r"어머니", r"아버지", r"아빠", r"보호자", r"지인"]
PROXY_FALSE_PATTERNS = [r"본인", r"저요", r"저입니다", r"제가", r"환자 본인", r"제가 받을", r"제가 진료"]

# Words that are never valid patient names (booking terms, dept names, filler words, etc.)
_NON_NAME_WORDS: frozenset[str] = frozenset({
    # Booking-related action words
    "예약", "취소", "변경", "확인", "조회", "접수",
    # Medical/visit terms
    "진료", "상담", "치료", "처방", "수술", "검사", "방문",
    # Department names
    "내과", "외과", "이비인후과", "정형외과", "피부과", "안과", "소아과", "치과", "응급",
    # Temporal expressions
    "오전", "오후", "오늘", "내일", "모레", "글피",
    # Identity / proxy terms
    "본인", "대리", "가족", "지인", "보호자", "환자",
    # Polite expressions / filler
    "부탁", "부탁해", "부탁드려", "감사", "안녕", "죄송", "실례",
    # Common conversational words
    "맞아요", "알겠습니다", "그리고", "그런데", "그러면", "아니요",
    # Korean pronouns / determiners that are not names
    "아무", "누구", "무엇", "어떤", "이런", "저런", "그런",
    # Short interrogative / exclamatory words
    "왜요", "네요", "혹시", "잠깐", "저기",
})

# Backward-compatible alias used by some tests/mocks.
classify_safety = safety_check


def _load_appointments_from_disk() -> list[dict]:
    return load_bookings()


def create_session(
    *,
    customer_name: str | None = None,
    birth_date: str | None = None,
    customer_type: str | None = None,
    all_appointments: list[dict] | None = None,
    context: dict | None = None,
) -> dict:
    return {
        "customer_name": customer_name,
        "birth_date": birth_date,
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
    if result_action in VALID_ACTIONS:
        return result_action
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
            return "clarify"

    return "clarify"


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
        score = 0.85
        if category in {"medical_advice", "off_topic", "emergency"}:
            score += 0.14
        if safety.get("unsupported_department") or safety.get("unsupported_doctor"):
            score += 0.1
        if category == "classification_error":
            score -= 0.25
        return _round_confidence(score)

    score = 0.6
    if classified_intent in VALID_ACTIONS:
        score += 0.1
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
        score -= 0.2
    if len(missing_info) >= 2:
        score -= 0.1
    if intent.get("error"):
        score -= 0.15
    if classified_intent != result_action and result_action in {"clarify", "reject", "escalate"}:
        score -= 0.05

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
    policy = policy_result or {}
    parts: list[str] = []

    if safety.get("unsupported_department"):
        parts.append(f"지원불가 분과({safety['unsupported_department']})")
        parts.append(f"Safety Gate: {result_action}")
        return ", ".join(parts)
    if safety.get("unsupported_doctor"):
        parts.append(f"미등록 의료진({safety['unsupported_doctor']})")
        parts.append(f"Safety Gate: {result_action}")
        return ", ".join(parts)

    category = safety.get("category", "safe")
    if category != "safe":
        reason_map = {
            "medical_advice": "의료 상담 요청",
            "off_topic": "예약 외 문의",
            "privacy_request": "개인정보 요청",
            "complaint": "불만/컴플레인",
            "operational_escalation": "운영 문의",
            "emergency": "응급 상황",
            "classification_error": "분류 실패",
        }
        if reason_map.get(category):
            parts.append(reason_map.get(category))

        parts.append(f"Safety Gate: {result_action}")
        return ", ".join(parts)

    parts.append("Safety Pass")

    if customer_type:
        type_map = {"new": "신규", "revisit": "재진", "초진": "신규", "재진": "재진"}
        parts.append(f"저장소 이력 확인({type_map.get(customer_type, customer_type)})")

    if classified_intent:
        intent_map = {
            "book_appointment": "예약",
            "modify_appointment": "변경",
            "cancel_appointment": "취소",
            "check_appointment": "조회",
        }
        parts.append(f"의도: {intent_map.get(classified_intent, classified_intent)}")

    if policy.get("reason"):
        parts.append(policy["reason"])
    elif policy.get("allowed") is False:
        parts.append("정책상 작업 거절")
    elif policy.get("allowed") is True:
        parts.append("정책 통과")

    if result_action != classified_intent and result_action in {"clarify", "escalate", "reject"}:
        action_map = {"clarify": "정보 확인", "escalate": "상담원 연결", "reject": "처리 불가"}
        parts.append(f"최종 액션: {action_map.get(result_action, result_action)}")

    return ", ".join(parts) if parts else "분류됨"


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
    response: str,
) -> dict:
    resolved_classified_intent = _determine_classified_intent(
        result_action=result_action,
        classified_intent=classified_intent,
        safety_result=safety_result,
        intent_result=intent_result,
    )
    final_action = result_action
    if final_action not in VALID_ACTIONS:
        final_action = resolved_classified_intent
        if final_action not in VALID_ACTIONS:
            final_action = "clarify"

    return {
        "ticket_id": (ticket or {}).get("ticket_id"),
        "classified_intent": resolved_classified_intent,
        "department": department,
        "action": final_action,
        "response": response,
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
            "customer_name": None,
            "patient_name": None,
            "patient_contact": None,
            "birth_date": None,
            "is_proxy_booking": None,
            "resolved_customer_type": None,
            "pending_confirmation": None,
            "pending_action": None,
            "pending_missing_info": [],
            "pending_missing_info_queue": [],
            "pending_candidates": None,
            "pending_alternative_slots": None,
            "clarify_turn_count": 0,
        }

    session_state.setdefault("conversation_history", [])
    session_state.setdefault("accumulated_slots", {"date": None, "time": None, "department": None})
    session_state.setdefault("customer_name", None)
    session_state.setdefault("patient_name", None)
    session_state.setdefault("patient_contact", None)
    session_state.setdefault("birth_date", None)
    session_state.setdefault("is_proxy_booking", None)
    session_state.setdefault("resolved_customer_type", None)
    session_state.setdefault("pending_confirmation", None)
    session_state.setdefault("pending_action", None)
    session_state.setdefault("pending_missing_info", [])
    session_state.setdefault("pending_missing_info_queue", list(session_state.get("pending_missing_info") or []))
    session_state.setdefault("pending_candidates", None)
    session_state.setdefault("pending_alternative_slots", None)
    session_state.setdefault("clarify_turn_count", 0)
    return session_state


def _record_history(session_state: dict | None, role: str, content: str) -> None:
    if session_state is None:
        return
    session_state["conversation_history"].append({"role": role, "content": content})


def _set_pending_missing_info(session_state: dict | None, missing_info: list[str]) -> None:
    if session_state is None:
        return
    deduped: list[str] = []
    for item in missing_info:
        if item and item not in deduped:
            deduped.append(item)
    session_state["pending_missing_info"] = deduped
    session_state["pending_missing_info_queue"] = deduped.copy()


def _get_pending_missing_info(session_state: dict | None) -> list[str]:
    if session_state is None:
        return []
    queue = session_state.get("pending_missing_info_queue")
    if isinstance(queue, list) and queue:
        return list(queue)
    pending = session_state.get("pending_missing_info") or []
    return list(pending)


def _increment_clarify_turn_count(session_state: dict | None) -> None:
    if session_state is None:
        return
    session_state["clarify_turn_count"] = int(session_state.get("clarify_turn_count") or 0) + 1


def _reset_clarify_turn_count(session_state: dict | None) -> None:
    if session_state is None:
        return
    session_state["clarify_turn_count"] = 0


def _prioritize_missing_info(missing_info: list[str]) -> list[str]:
    priority = {
        "is_proxy_booking": 0,
        "patient_name": 1,
        "patient_contact": 2,
        "department": 3,
        "date": 4,
        "time": 5,
        "birth_date": 6,
        "appointment_target": 7,
        "slot_selection": 8,
        "confirmation": 9,
        "customer_name": 10,
    }
    deduped: list[str] = []
    for item in missing_info:
        if item and item not in deduped:
            deduped.append(item)
    return sorted(deduped, key=lambda item: (priority.get(item, 99), deduped.index(item)))


def _should_escalate_for_clarify_limit(session_state: dict | None, missing_info: list[str]) -> bool:
    if session_state is None:
        return False
    if int(session_state.get("clarify_turn_count") or 0) < 4:
        return False
    core_info = {
        "is_proxy_booking",
        "patient_name",
        "patient_contact",
        "department",
        "date",
        "time",
        "birth_date",
        "appointment_target",
    }
    return any(item in core_info for item in missing_info)


def _clear_dialogue_state(session_state: dict | None) -> None:
    if session_state is None:
        return
    session_state["accumulated_slots"] = {"date": None, "time": None, "department": None}
    session_state["pending_confirmation"] = None
    session_state["pending_action"] = None
    _set_pending_missing_info(session_state, [])
    session_state["pending_candidates"] = None
    session_state["pending_alternative_slots"] = None
    session_state["is_proxy_booking"] = None
    session_state["patient_name"] = None
    session_state["patient_contact"] = None
    session_state["birth_date"] = None
    _reset_clarify_turn_count(session_state)


def _reset_pending_flow_for_new_action(session_state: dict | None, new_action: str | None = None) -> None:
    if session_state is None:
        return
    session_state["accumulated_slots"] = {"date": None, "time": None, "department": None}
    session_state["pending_confirmation"] = None
    session_state["pending_candidates"] = None
    session_state["pending_alternative_slots"] = None
    _set_pending_missing_info(session_state, [])
    if new_action:
        session_state["pending_action"] = new_action
    _reset_clarify_turn_count(session_state)


def _build_response_and_record(session_state: dict | None, **kwargs) -> dict:
    ticket = kwargs.pop("ticket", None)
    classified_intent = kwargs.pop("classified_intent", None)
    safety_result = kwargs.pop("safety_result", None)
    intent_result = kwargs.pop("intent_result", None)
    policy_result = kwargs.pop("policy_result", None)
    customer_type = kwargs.pop("customer_type", None)

    result = build_response(**kwargs)
    if (
        safety_result
        and safety_result.get("contains_booking_subrequest")
        and result.get("action") in {"book_appointment", "modify_appointment", "cancel_appointment", "check_appointment", "clarify"}
    ):
        result["response"] = (
            "의료 상담(진단, 약물, 치료 방법 안내)은 도와드릴 수 없습니다. "
            + result.get("response", "")
        )
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
            response=result.get("response"),
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


def _classify_intent_with_optional_now(user_message: str, now: datetime, conversation_history: list[dict] | None = None) -> dict:
    if isinstance(classify_intent, Mock):
        return classify_intent(user_message)
    try:
        return classify_intent(user_message, now=now, conversation_history=conversation_history)
    except TypeError:
        return classify_intent(user_message)


_TICKET_CUSTOMER_TYPE_MAP = {
    "초진": "초진", "first_visit": "초진", "first": "초진", "new": "초진",
    "재진": "재진", "returning": "재진", "follow_up": "재진", "follow-up": "재진", "revisit": "재진",
}
_SUPPORTED_DEPARTMENTS = {"이비인후과", "내과", "정형외과"}


def _merge_ticket_context_into_intent(ticket: dict, intent_result: dict) -> dict:
    """배치 모드 전용: ticket 구조화 메타데이터로 intent 누락 필드를 채운다.

    LLM이 message 텍스트에서 추출하지 못한 customer_type / preferred_* 필드를
    ticket 에 이미 정리된 값으로 보완하여 불필요한 clarify 응답을 방지한다.
    """
    context = ticket.get("context") or {}
    result = dict(intent_result)

    # customer_type 보완
    if not result.get("customer_type") and ticket.get("customer_type"):
        normalized = _TICKET_CUSTOMER_TYPE_MAP.get(str(ticket["customer_type"]).strip())
        if normalized:
            result["customer_type"] = normalized

    # 분과 보완
    if not result.get("department"):
        preferred_dept = context.get("preferred_department")
        if preferred_dept in _SUPPORTED_DEPARTMENTS:
            result["department"] = preferred_dept

    # 날짜/시간 보완
    if not result.get("date") and context.get("preferred_date"):
        result["date"] = str(context["preferred_date"])
    if not result.get("time") and context.get("preferred_time"):
        result["time"] = str(context["preferred_time"])

    # 보완된 필드를 missing_info 에서 제거
    filled = set()
    for field in list(result.get("missing_info") or []):
        if field == "customer_type" and result.get("customer_type"):
            filled.add(field)
        elif field == "department" and result.get("department"):
            filled.add(field)
        elif field == "date" and result.get("date"):
            filled.add(field)
        elif field == "time" and result.get("time"):
            filled.add(field)
    result["missing_info"] = [f for f in (result.get("missing_info") or []) if f not in filled]

    # 모든 필수 슬롯이 채워졌으면 action 을 clarify → book_appointment 로 업그레이드
    if result.get("action") == "clarify" and not result["missing_info"]:
        classified = result.get("classified_intent") or ""
        all_slots_ready = (
            result.get("department") and result.get("date")
            and result.get("time") and result.get("customer_type")
        )
        if classified == "book_appointment" or all_slots_ready:
            result["action"] = "book_appointment"
            result["classified_intent"] = "book_appointment"

    return result


def _run_safety_gate(user_message: str, session_state: dict | None = None) -> dict:
    if session_state is not None:
        if session_state.get("pending_confirmation") and (_is_affirmative(user_message) or _is_negative(user_message)):
            return {"category": "safe"}

        if session_state.get("pending_candidates") and re.fullmatch(r"\d+번(이요|이에요|으로|로)?", _normalize_text(user_message)):
            return {"category": "safe"}

        if session_state.get("pending_alternative_slots") and re.fullmatch(r"\d+번?(이요|이에요|으로|로)?", _normalize_text(user_message)):
            return {"category": "safe"}

        pending_missing_info = _get_pending_missing_info(session_state)
        if "is_proxy_booking" in pending_missing_info and _parse_proxy_answer(user_message) is not None:
            return {"category": "safe"}

        # 대화 진행 중 정보 수집 단계: 먼저 안전 검사를 수행하고,
        # 위험하지 않은 경우에만 fast-path 적용
        if pending_missing_info and any(
            f in pending_missing_info
            for f in ["patient_name", "patient_contact", "customer_name", "birth_date", "department", "date", "time"]
        ):
            full_check = _normalize_safety_result(classify_safety(user_message))
            # 안전 통과 여부와 무관하게 full_check 결과를 그대로 반환하여
            # unsupported_department / unsupported_doctor 등 부가 필드가 유지되도록 한다.
            return full_check

    return _normalize_safety_result(classify_safety(user_message))


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
    
    record_kpi_event(KpiEvent.SAFE_REJECT)

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

    if category == "privacy_request":
        return _build_response_and_record(
            session_state,
            action="reject",
            message="다른 환자의 예약 정보나 개인정보는 안내해 드릴 수 없습니다.",
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

    if category == "operational_escalation":
        return _build_response_and_record(
            session_state,
            action="escalate",
            message="해당 문의는 상담원이 확인 후 안내드려야 합니다. 상담원 연결을 도와드릴게요.",
            ticket=ticket,
            safety_result=safety_result,
            customer_type=customer_type,
        )

    if category == "classification_error" and fallback_action == "clarify":
        record_kpi_event(KpiEvent.AGENT_SOFT_FAIL_CLARIFY)
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
        record_kpi_event(KpiEvent.SAFE_REJECT)
        return _build_response_and_record(
            session_state,
            action="reject",
            message=f"해당 분과는 코비메디에서 지원하지 않습니다. 현재 예약 가능한 분과는 이비인후과, 내과, 정형외과입니다.",
            ticket=ticket,
            safety_result=safety_result,
            customer_type=customer_type,
        )

    if unsupported_doctor:
        record_kpi_event(KpiEvent.SAFE_REJECT)
        return _build_response_and_record(
            session_state,
            action="reject",
            message=f"해당 의사는 코비메디에서 지원하지 않습니다. 현재 확인 가능한 의료진은 이춘영 원장, 김만수 원장, 원징수 원장입니다.",
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
    record_kpi_event(KpiEvent.AGENT_SOFT_FAIL_CLARIFY)
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
        "contains_booking_subrequest": False,
        "safe_booking_text": None,
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


def _sync_queue_with_accumulated_slots(session_state: dict | None, merged_slots: dict) -> None:
    """D-001 fix: pending_missing_info_queue에서 이미 채워진 슬롯 항목을 제거한다.

    classify_intent가 department/date/time을 추출해 accumulated_slots에 반영했음에도
    pending_missing_info_queue가 해당 항목을 여전히 포함하는 경우를 방지한다.
    _determine_dialogue_missing_info가 최종 판정 기준이지만, 큐를 미리 동기화하여
    LLM 결과의 edge case로 인한 불일치를 방어적으로 차단한다.
    """
    if session_state is None:
        return
    queue = list(session_state.get("pending_missing_info_queue") or [])
    if not queue:
        return
    new_queue = [
        item for item in queue
        if not (
            (item == "department" and merged_slots.get("department"))
            or (item == "date" and merged_slots.get("date"))
            or (item == "time" and merged_slots.get("time"))
        )
    ]
    if len(new_queue) != len(queue):
        session_state["pending_missing_info_queue"] = new_queue
        session_state["pending_missing_info"] = new_queue.copy()


def _get_effective_customer_name(ticket: dict | None, session_state: dict | None) -> str | None:
    return (ticket or {}).get("customer_name") or (session_state or {}).get("customer_name")


def _get_effective_birth_date(ticket: dict | None, session_state: dict | None) -> str | None:
    raw_birth_date = (ticket or {}).get("birth_date") or (session_state or {}).get("birth_date")
    return normalize_birth_date(raw_birth_date)


def _format_patient_contact(value: str | None) -> str | None:
    normalized = normalize_patient_contact(value)
    if not normalized:
        return None
    if len(normalized) == 11:
        return f"{normalized[:3]}-{normalized[3:7]}-{normalized[7:]}"
    if len(normalized) == 10:
        return f"{normalized[:3]}-{normalized[3:6]}-{normalized[6:]}"
    return normalized


def _extract_patient_contact(text: str | None) -> str | None:
    normalized = _normalize_text(text)
    if not normalized:
        return None
    match = re.search(r"01[0-9][- ]?\d{3,4}[- ]?\d{4}", normalized)
    if not match:
        digits_only = re.sub(r"\D", "", normalized)
        if digits_only.startswith("01") and len(digits_only) in {10, 11}:
            return _format_patient_contact(digits_only)
        return None
    return _format_patient_contact(match.group(0))


def _parse_proxy_answer(text: str | None) -> bool | None:
    normalized = _normalize_text(text)
    if not normalized:
        return None
    if any(re.search(pattern, normalized) for pattern in PROXY_TRUE_PATTERNS):
        return True
    if any(re.search(pattern, normalized) for pattern in PROXY_FALSE_PATTERNS):
        return False
    return None


def _get_effective_patient_name(ticket: dict | None, session_state: dict | None) -> str | None:
    return (
        (ticket or {}).get("patient_name")
        or (session_state or {}).get("patient_name")
        or ((ticket or {}).get("customer_name") if session_state is None else None)
    )


def _get_effective_patient_contact(ticket: dict | None, session_state: dict | None) -> str | None:
    return _format_patient_contact((ticket or {}).get("patient_contact") or (session_state or {}).get("patient_contact"))


def _get_effective_proxy_booking(ticket: dict | None, session_state: dict | None) -> bool | None:
    if (ticket or {}).get("is_proxy_booking") is not None:
        return bool(ticket.get("is_proxy_booking"))
    if (session_state or {}).get("is_proxy_booking") is not None:
        return bool(session_state.get("is_proxy_booking"))
    return None


def _sync_identity_state_from_intent(ticket: dict, session_state: dict | None, intent_result: dict, *, is_chat: bool) -> None:
    if session_state is None and not ticket:
        return

    patient_name = intent_result.get("patient_name")
    patient_contact = _format_patient_contact(intent_result.get("patient_contact"))
    birth_date = normalize_birth_date(intent_result.get("birth_date"))
    proxy_flag = intent_result.get("is_proxy_booking")

    if proxy_flag is not None:
        # Bug fix (F-031): In chat mode, LLM must NOT assume is_proxy_booking=False.
        # The user must explicitly confirm self-booking vs proxy-booking.
        # Only trust True (clear proxy signal extracted from message) in chat mode.
        # False is accepted only in batch mode or if user explicitly confirmed it via
        # _consume_pending_identity_input (which sets session_state directly).
        if not (is_chat and proxy_flag is False):
            ticket["is_proxy_booking"] = bool(proxy_flag)
            if session_state is not None:
                session_state["is_proxy_booking"] = bool(proxy_flag)

    if patient_name:
        ticket["patient_name"] = patient_name
        if session_state is not None:
            session_state["patient_name"] = patient_name

    if patient_contact:
        ticket["patient_contact"] = patient_contact
        if session_state is not None:
            session_state["patient_contact"] = patient_contact

    if birth_date:
        ticket["birth_date"] = birth_date
        if session_state is not None:
            session_state["birth_date"] = birth_date

    if session_state is None and not ticket.get("patient_name") and ticket.get("customer_name") and not ticket.get("is_proxy_booking"):
        ticket["patient_name"] = ticket.get("customer_name")

    if session_state is not None and is_chat and session_state.get("is_proxy_booking") is False and not session_state.get("patient_name") and ticket.get("customer_name"):
        session_state["patient_name"] = ticket.get("customer_name")


def _extract_patient_name(text: str | None) -> str | None:
    normalized = _normalize_text(text)
    if not normalized:
        return None

    match = re.search(r"(?:제 이름은|이름은|저는|환자 이름은)\s*([가-힣A-Za-z]{2,20})", normalized)
    if match:
        cleaned = re.sub(r"(?:입니다|이에요|예요|이요|요)$", "", match.group(1)).strip(" .,!")
        if cleaned and cleaned not in _NON_NAME_WORDS:
            return cleaned

    text_without_phone = re.sub(r"01[0-9][- ]?\d{3,4}[- ]?\d{4}", "", normalized)
    clean_text = re.sub(r"[^가-힣A-Za-z\s]", " ", text_without_phone)

    for token in clean_text.split():
        token = re.sub(r"(?:입니다|이에요|예요|이요|요)$", "", token).strip()
        if (
            2 <= len(token) <= 4
            and re.fullmatch(r"[가-힣]{2,4}", token)
            and token not in _NON_NAME_WORDS
        ):
            return token

    return None


def _extract_birth_date(text: str | None) -> str | None:
    normalized = _normalize_text(text)
    if not normalized:
        return None

    candidates = [normalized]
    pattern_match = re.search(r"(\d{4}[./-]\d{1,2}[./-]\d{1,2}|\d{8}|\d{4}년\s*\d{1,2}월\s*\d{1,2}일)", normalized)
    if pattern_match:
        candidates.insert(0, pattern_match.group(1))

    for candidate in candidates:
        parsed = normalize_birth_date(candidate)
        if parsed:
            return parsed
    return None


def _resolve_history_customer_type(
    customer_name: str | None,
    birth_date: str | None,
    patient_contact: str | None = None,
) -> dict:
    if not customer_name and not patient_contact:
        return {
            "customer_type": None,
            "ambiguous": False,
            "birth_date_candidates": [],
            "matched_bookings": [],
            "has_non_cancelled_history": False,
            "has_cancelled_history": False,
        }

    try:
        return resolve_customer_type_from_history(
            customer_name,
            birth_date=birth_date,
            patient_contact=patient_contact,
        )
    except Exception:
        return {
            "customer_type": None,
            "ambiguous": False,
            "birth_date_candidates": [],
            "matched_bookings": [],
            "has_non_cancelled_history": False,
            "has_cancelled_history": False,
        }


def _determine_missing_info(
    action: str,
    slots: dict,
    customer_name: str | None = None,
    birth_date: str | None = None,
    history_resolution: dict | None = None,
) -> list[str]:
    if action != "book_appointment":
        return []

    missing = []
    if not slots.get("department"):
        missing.append("department")
    if not slots.get("date"):
        missing.append("date")
    if not slots.get("time"):
        missing.append("time")
    if not customer_name:
        missing.append("customer_name")
        return missing
    if history_resolution and history_resolution.get("ambiguous") and not birth_date:
        missing.append("birth_date")
    return missing


def _determine_dialogue_missing_info(
    *,
    action: str,
    slots: dict,
    is_chat: bool,
    is_proxy_booking: bool | None,
    patient_name: str | None,
    patient_contact: str | None,
    customer_name: str | None,
    birth_date: str | None,
    history_resolution: dict | None,
) -> list[str]:
    if action in BOOKING_RELATED_ACTIONS and is_chat and is_proxy_booking is None:
        return ["is_proxy_booking"]

    missing: list[str] = []

    if action not in BOOKING_RELATED_ACTIONS:
        return []

    if is_chat and is_proxy_booking is None:
        missing.append("is_proxy_booking")

    effective_name = patient_name or (customer_name if is_proxy_booking is False else None)
    if is_chat:
        if not effective_name:
            missing.append("patient_name")
        if not patient_contact:
            missing.append("patient_contact")
    else:
        if is_proxy_booking and not patient_name:
            missing.append("patient_name")
        if is_proxy_booking and not patient_contact:
            missing.append("patient_contact")

    if action == "book_appointment":
        if not slots.get("department"):
            missing.append("department")
        if not slots.get("date"):
            missing.append("date")
        if not slots.get("time"):
            missing.append("time")

    if not customer_name and patient_name and is_proxy_booking is False:
        customer_name = patient_name

    if action == "book_appointment" and not customer_name and not patient_name and not is_chat:
        missing.append("customer_name")

    if history_resolution and history_resolution.get("ambiguous") and not birth_date:
        missing.append("birth_date")

    return _prioritize_missing_info(missing)


def _build_alternative_slot_question(alternative_slots: list[str], now: datetime) -> str:
    options: list[str] = []
    for index, slot in enumerate(alternative_slots, start=1):
        options.append(f"{index}) {build_success_message('check_appointment', appointment={'booking_time': slot}, now=now).replace('확인된 예약은 ', '').replace('입니다.', '')}")
    return "요청하신 시간은 어렵습니다. 가능한 다른 시간 중 원하시는 번호를 선택해주세요. " + ", ".join(options)


def _resolve_alternative_slot_selection(message: str, alternative_slots: list[str]) -> str | None:
    text = _normalize_text(message)
    number_match = re.search(r"(\d+)", text)
    if not number_match:
        return None
    index = int(number_match.group(1)) - 1
    if 0 <= index < len(alternative_slots):
        return alternative_slots[index]
    return None


def _update_slots_from_booking_time(session_state: dict | None, booking_time: str, now: datetime) -> dict:
    appointment_like = {"booking_time": booking_time}
    slots = _extract_candidate_slots(appointment_like, now)
    if session_state is not None:
        current = session_state.get("accumulated_slots", {})
        session_state["accumulated_slots"] = {
            "department": current.get("department"),
            "date": slots.get("date"),
            "time": slots.get("time"),
        }
        return dict(session_state["accumulated_slots"])
    return slots


def _consume_pending_identity_input(
    user_message: str,
    ticket: dict,
    session_state: dict | None,
) -> tuple[dict | None, dict | None]:
    if session_state is None:
        return None, None

    pending_missing_info = _get_pending_missing_info(session_state)
    if not pending_missing_info:
        return None, None

    # Intent-switch detection: if the user clearly requests a *different*
    # booking-related action while we are mid-flow collecting identity info,
    # reset all pending state and let the main flow re-classify from scratch.
    # This allows switching from e.g. cancel → book or book → cancel naturally.
    current_pending_action = session_state.get("pending_action")
    if current_pending_action in BOOKING_RELATED_ACTIONS:
        inferred_switch = _infer_requested_action(user_message)
        if (
            inferred_switch is not None
            and inferred_switch in BOOKING_RELATED_ACTIONS
            and inferred_switch != current_pending_action
        ):
            _reset_pending_flow_for_new_action(session_state, inferred_switch)
            return None, None

    # ── is_proxy_booking: 패턴 매칭으로 확정 처리 (LLM 불필요) ──
    if "is_proxy_booking" in pending_missing_info and _get_effective_proxy_booking(ticket, session_state) is None:
        parsed_proxy = _parse_proxy_answer(user_message)
        if parsed_proxy is None:
            _increment_clarify_turn_count(session_state)
            if _should_escalate_for_clarify_limit(session_state, ["is_proxy_booking"]):
                record_kpi_event(KpiEvent.AGENT_HARD_FAIL)
                return (
                    _build_response_and_record(
                        session_state,
                        action="escalate",
                        message="본인 여부 확인이 반복되어 상담원이 이어서 도와드리겠습니다.",
                        ticket=ticket,
                        classified_intent="escalate",
                        intent_result={"action": "escalate", "missing_info": ["is_proxy_booking"]},
                        customer_type=None,
                    ),
                    None,
                )
            record_kpi_event(KpiEvent.AGENT_SOFT_FAIL_CLARIFY)
            return (
                _build_response_and_record(
                    session_state,
                    action="clarify",
                    message=build_missing_info_question(["is_proxy_booking"], action_context=session_state.get("pending_action")),
                    ticket=ticket,
                    classified_intent="clarify",
                    intent_result={"action": "clarify", "missing_info": ["is_proxy_booking"]},
                    customer_type=None,
                ),
                None,
            )
        session_state["is_proxy_booking"] = parsed_proxy
        ticket["is_proxy_booking"] = parsed_proxy
        if parsed_proxy is False and not session_state.get("patient_name"):
            inferred_name = ticket.get("customer_name") or session_state.get("customer_name")
            if inferred_name:
                session_state["patient_name"] = inferred_name
                ticket["patient_name"] = inferred_name
        pending_missing_info = [item for item in pending_missing_info if item != "is_proxy_booking"]
        if not session_state.get("patient_name") and parsed_proxy is False:
            pending_missing_info = [item for item in pending_missing_info if item != "patient_name"]
        session_state["pending_missing_info_queue"] = list(pending_missing_info)
        session_state["pending_missing_info"] = list(pending_missing_info)
        _reset_clarify_turn_count(session_state)
        # proxy 처리 후, 나머지 필드는 classify_intent(대화 이력 포함)에서 추출
        return None, None

    # ── 나머지 identity/booking 필드: classify_intent에 위임 (대화 이력 전체 활용) ──
    # regex 기반 개별 추출 대신, LLM이 전체 대화에서 한번에 추출하도록 위임
    return None, None


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


def _appointment_identity(appointment: dict) -> tuple:
    return (
        appointment.get("id"),
        appointment.get("customer_name"),
        appointment.get("booking_time"),
        appointment.get("date"),
        appointment.get("time"),
        appointment.get("department"),
    )


def _merge_appointment_sources(*appointment_groups: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen: set[tuple] = set()

    for group in appointment_groups:
        for appointment in group or []:
            if not isinstance(appointment, dict):
                continue
            identity = _appointment_identity(appointment)
            if identity in seen:
                continue
            seen.add(identity)
            merged.append(appointment)

    return merged


def _load_storage_appointments(customer_name: str | None, birth_date: str | None = None) -> list[dict]:
    if not customer_name:
        return []
    try:
        filters = {"birth_date": birth_date} if birth_date else None
        return find_bookings(customer_name=customer_name, filters=filters)
    except Exception:
        return []


def _find_customer_appointments(ticket: dict, all_appointments: list[dict], existing_appointment: dict | None) -> list[dict]:
    customer_name = ticket.get("customer_name")
    patient_name = ticket.get("patient_name") or customer_name
    birth_date = normalize_birth_date(ticket.get("birth_date"))
    patient_contact = _format_patient_contact(ticket.get("patient_contact"))

    try:
        if patient_contact:
            storage_matches = find_bookings(patient_contact=patient_contact)
        else:
            storage_matches = _load_storage_appointments(patient_name, birth_date)
    except Exception:
        storage_matches = []

    matches = []
    for appointment in all_appointments:
        appointment_contact = _format_patient_contact(appointment.get("patient_contact"))
        appointment_name = appointment.get("patient_name") or appointment.get("customer_name")
        appointment_birth_date = normalize_birth_date(appointment.get("birth_date"))

        if patient_contact:
            if appointment_contact != patient_contact:
                continue
        else:
            if patient_name and appointment_name != patient_name and appointment.get("customer_name") != patient_name:
                continue
            if birth_date and appointment_birth_date != birth_date:
                continue
        matches.append(appointment)

    merged_matches = _merge_appointment_sources(storage_matches, matches)
    if merged_matches:
        return merged_matches

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
    all_appointments: list[dict],
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
        if action == "book_appointment":
            try:
                persisted_booking = create_booking(appointment)
            except Exception:
                record_kpi_event(KpiEvent.AGENT_HARD_FAIL)
                return _build_response_and_record(
                    session_state,
                    action="clarify",
                    message="예약 정보를 저장하는 중 문제가 발생했습니다. 다시 한 번 확인해 드릴까요?",
                    department=appointment.get("department"),
                    ticket=ticket,
                    classified_intent="book_appointment",
                    intent_result={
                        "action": "book_appointment",
                        "department": appointment.get("department"),
                        "date": appointment.get("date"),
                        "time": appointment.get("time"),
                    },
                    customer_type=customer_type or appointment.get("customer_type"),
                )
            all_appointments.append(persisted_booking)
            appointment = persisted_booking
        message = build_success_message(action, department=appointment.get("department"), appointment=appointment, now=now)
        _clear_dialogue_state(session_state)
        record_kpi_event(KpiEvent.AGENT_SUCCESS)
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
        # D-010 fix: 확인 거부는 유효한 대화 진전(사용자가 날짜/시간 변경을 원함)이므로
        # clarify_turn_count를 리셋하여 이후 턴에서 불필요한 escalate를 방지한다.
        _reset_clarify_turn_count(session_state)
        record_kpi_event(KpiEvent.AGENT_SOFT_FAIL_CLARIFY)
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
    is_chat = session_state is not None

    state = _init_session_state(session_state) if is_chat else None
    ticket = dict(ticket)
    if ticket.get("birth_date"):
        ticket["birth_date"] = normalize_birth_date(ticket.get("birth_date"))
    customer_type = ticket.get("customer_type")
    if state is not None:
        if ticket.get("customer_name"):
            state["customer_name"] = ticket.get("customer_name")
        if ticket.get("birth_date"):
            state["birth_date"] = normalize_birth_date(ticket.get("birth_date"))
            ticket["birth_date"] = state.get("birth_date")
        if ticket.get("patient_name"):
            state["patient_name"] = ticket.get("patient_name")
        if ticket.get("patient_contact"):
            state["patient_contact"] = _format_patient_contact(ticket.get("patient_contact"))
        if ticket.get("is_proxy_booking") is not None:
            state["is_proxy_booking"] = bool(ticket.get("is_proxy_booking"))
    existing_appointment = _resolve_existing_appointment_from_ticket(
        ticket,
        all_appointments,
        existing_appointment,
        now,
    )
    user_message = ticket.get("message")
    if not user_message:
        record_kpi_event(KpiEvent.AGENT_HARD_FAIL)
        return _build_response_and_record(
            state,
            action="reject",
            message="문의 내용이 없습니다.",
            ticket=ticket,
            classified_intent="reject",
            customer_type=customer_type,
        )

    _record_history(state, "user", user_message)

    safety_result = _run_safety_gate(user_message, state)

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

    if state is not None and state.get("pending_alternative_slots"):
        selected_slot = _resolve_alternative_slot_selection(user_message, state["pending_alternative_slots"])
        if selected_slot is None:
            _increment_clarify_turn_count(state)
            record_kpi_event(KpiEvent.AGENT_SOFT_FAIL_CLARIFY)
            return _build_response_and_record(
                state,
                action="clarify",
                message=_build_alternative_slot_question(state["pending_alternative_slots"], now),
                ticket=ticket,
                classified_intent=state.get("pending_action") or "clarify",
                intent_result={"action": state.get("pending_action") or "clarify", "missing_info": ["slot_selection"]},
                customer_type=customer_type,
            )
        merged_slots = _update_slots_from_booking_time(state, selected_slot, now)
        ticket["booking_time"] = selected_slot
        ticket["date"] = merged_slots.get("date")
        ticket["time"] = merged_slots.get("time")
        state["pending_alternative_slots"] = None

    if state is not None:
        pending_confirmation_result = _handle_pending_confirmation(
            user_message,
            state,
            all_appointments,
            now,
            ticket=ticket,
            customer_type=customer_type,
        )
        if pending_confirmation_result is not None:
            return pending_confirmation_result

    if state is not None and state.get("pending_candidates"):
        pending_action = state.get("pending_action") or "check_appointment"
        selected_existing_appointment = _resolve_candidate_selection(user_message, state["pending_candidates"], now)
        if selected_existing_appointment is None:
            _increment_clarify_turn_count(state)
            message = build_appointment_options_question(pending_action, state["pending_candidates"], now)
            record_kpi_event(KpiEvent.AGENT_SOFT_FAIL_CLARIFY)
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
    else:
        selected_existing_appointment = None
        action_override = None

    consumed_identity_response, forced_intent_result = _consume_pending_identity_input(user_message, ticket, state)
    if consumed_identity_response is not None:
        return consumed_identity_response

    if forced_intent_result is not None:
        intent_result = forced_intent_result
    elif selected_existing_appointment is None:
        classification_message = safety_result.get("safe_booking_text") or user_message
        chat_history = (state or {}).get("conversation_history") if is_chat else None
        intent_result = _classify_intent_with_optional_now(classification_message, now, conversation_history=chat_history)
        if intent_result.get("error"):
            fallback_action = intent_result.get("fallback_action")
            fallback_message = intent_result.get("fallback_message")
            record_kpi_event(KpiEvent.AGENT_HARD_FAIL)
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
        slots = _extract_candidate_slots(selected_existing_appointment, now)
        intent_result = {
            "action": action_override,
            "department": slots.get("department"),
            "date": slots.get("date"),
            "time": slots.get("time"),
            "missing_info": [],
        }

    # 배치 모드: ticket 메타데이터(context.preferred_*, customer_type)로 intent 보완
    if not is_chat:
        intent_result = _merge_ticket_context_into_intent(ticket, intent_result)

    _sync_identity_state_from_intent(ticket, state, intent_result, is_chat=is_chat)

    action = intent_result.get("action")
    inferred_action = _infer_requested_action(user_message)
    if action == "clarify":
        pending_action = (state or {}).get("pending_action")
        has_booking_slots = any(intent_result.get(key) for key in ["department", "date", "time"])
        if pending_action:
            action = pending_action
        elif inferred_action in {"cancel_appointment", "modify_appointment", "check_appointment"}:
            action = inferred_action
        elif inferred_action == "book_appointment" and (is_chat or has_booking_slots):
            # Bug fix (F-031, F-042): In chat mode, always upgrade to book_appointment
            # when user clearly requests booking even without slots yet (e.g. "예약할래요")
            # so the proxy question is asked on the first turn.
            # In batch mode (is_chat=False), only upgrade when at least one booking slot
            # (department/date/time) is already present in the classified intent — this
            # continues a concrete booking flow while keeping the generic
            # "더 자세한 정보가 필요합니다" message for fully under-specified requests.
            action = inferred_action

    previous_pending_action = (state or {}).get("pending_action")
    if (
        state is not None
        and previous_pending_action in BOOKING_RELATED_ACTIONS
        and action in BOOKING_RELATED_ACTIONS
        and previous_pending_action != action
    ):
        _reset_pending_flow_for_new_action(state, action)

    merged_slots = _merge_accumulated_slots(state, intent_result)
    # D-001 fix: accumulated_slots에 반영된 슬롯을 pending_missing_info_queue에서 즉시 제거
    _sync_queue_with_accumulated_slots(state, merged_slots)
    department = merged_slots.get("department") or intent_result.get("department") or safety_result.get("department_hint")
    classified_intent = intent_result.get("action")

    if state is not None:
        state["pending_action"] = action

    effective_proxy_booking = _get_effective_proxy_booking(ticket, state)
    effective_patient_name = _get_effective_patient_name(ticket, state)
    effective_patient_contact = _get_effective_patient_contact(ticket, state)
    effective_customer_name = _get_effective_customer_name(ticket, state)
    if not effective_customer_name and effective_patient_name and effective_proxy_booking is False:
        effective_customer_name = effective_patient_name
        ticket["customer_name"] = effective_customer_name
        if state is not None:
            state["customer_name"] = effective_customer_name
    effective_birth_date = _get_effective_birth_date(ticket, state)

    history_resolution = None
    if action in BOOKING_RELATED_ACTIONS:
        history_resolution = _resolve_history_customer_type(
            effective_patient_name or effective_customer_name,
            effective_birth_date,
            patient_contact=effective_patient_contact,
        )
        if state is not None and effective_customer_name:
            state["customer_name"] = effective_customer_name
        if state is not None and effective_birth_date:
            state["birth_date"] = effective_birth_date
        if state is not None and effective_patient_name:
            state["patient_name"] = effective_patient_name
        if state is not None and effective_patient_contact:
            state["patient_contact"] = effective_patient_contact

    dialogue_missing_info = _determine_dialogue_missing_info(
        action=action,
        slots=merged_slots,
        is_chat=is_chat,
        is_proxy_booking=effective_proxy_booking,
        patient_name=effective_patient_name,
        patient_contact=effective_patient_contact,
        customer_name=effective_customer_name,
        birth_date=effective_birth_date,
        history_resolution=history_resolution,
    )
    if dialogue_missing_info:
        if _should_escalate_for_clarify_limit(state, dialogue_missing_info):
            record_kpi_event(KpiEvent.AGENT_HARD_FAIL)
            return _build_response_and_record(
                state,
                action="escalate",
                message="여러 차례 확인이 필요해 상담원이 이어서 도와드리는 것이 안전합니다. 상담원 연결을 도와드릴게요.",
                department=department,
                ticket=ticket,
                classified_intent=classified_intent,
                safety_result=safety_result,
                intent_result={**intent_result, "action": action, "department": department, "missing_info": dialogue_missing_info},
                customer_type=customer_type,
            )
        _set_pending_missing_info(state, dialogue_missing_info)
        _increment_clarify_turn_count(state)
        record_kpi_event(KpiEvent.AGENT_SOFT_FAIL_CLARIFY)
        return _build_response_and_record(
            state,
            action="clarify",
            message=build_missing_info_question(dialogue_missing_info, department=department, action_context=action),
            department=department,
            ticket=ticket,
            classified_intent=classified_intent,
            safety_result=safety_result,
            intent_result={**intent_result, "action": action, "department": department, "missing_info": dialogue_missing_info},
            customer_type=customer_type,
        )

    _set_pending_missing_info(state, [])

    if action == "clarify":
        if department:
            message = f"{department} 예약을 도와드릴 수 있습니다. 원하시는 날짜와 시간을 알려주시겠어요?"
        else:
            message = "예약 관련하여 더 자세한 정보가 필요합니다. 원하시는 날짜, 시간, 진료과를 알려주시겠어요?"
        _increment_clarify_turn_count(state)
        record_kpi_event(KpiEvent.AGENT_SOFT_FAIL_CLARIFY)
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

    if action == "book_appointment":
        customer_type = (history_resolution or {}).get("customer_type")
        if state is not None:
            state["resolved_customer_type"] = customer_type
        if customer_type:
            ticket["customer_type"] = customer_type

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
            record_kpi_event(KpiEvent.AGENT_SOFT_FAIL_CLARIFY)
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

    history = _resolve_history_customer_type(effective_customer_name, effective_birth_date, effective_patient_contact)
    customer_type = history.get("customer_type", "new")
    
    policy_ticket = PolicyTicket(
        intent=action,
        user=User(
            patient_id=ticket.get("customer_id") or "unknown",
            name=effective_customer_name,
            is_first_visit=(customer_type not in {"재진", "revisit"}),
        ),
        context={
            "appointment_time": booking_time,
            "booking_id": (target_existing_appointment or {}).get("id"),
            "new_appointment_time": booking_time,
        },
    )

    policy_result = apply_policy(policy_ticket, all_appointments, now)

    if not policy_result.action.value.endswith("appointment"):
        message = policy_result.message
        recommended_action = policy_result.action.value
        alternatives = policy_result.suggested_slots

        if alternatives and is_chat and recommended_action == "clarify":
            if _should_escalate_for_clarify_limit(state, ["slot_selection"]):
                record_kpi_event(KpiEvent.AGENT_HARD_FAIL)
                return _build_response_and_record(
                    state,
                    action="escalate",
                    message="대체 시간 확인이 길어져 상담원이 이어서 도와드리겠습니다.",
                    department=department,
                    ticket=ticket,
                    classified_intent=classified_intent,
                    safety_result=safety_result,
                    intent_result=intent_result,
                    policy_result={"reason": message, "allowed": False},
                    customer_type=customer_type,
                )
            state["pending_alternative_slots"] = alternatives
            _set_pending_missing_info(state, ["slot_selection"])
            _increment_clarify_turn_count(state)
            record_kpi_event(KpiEvent.AGENT_SOFT_FAIL_CLARIFY)
            return _build_response_and_record(
                state,
                action="clarify",
                message=_build_alternative_slot_question(alternatives, now),
                department=department,
                ticket=ticket,
                classified_intent=classified_intent,
                safety_result=safety_result,
                intent_result={**intent_result, "missing_info": ["slot_selection"]},
                policy_result={"reason": message, "allowed": False},
                customer_type=customer_type,
            )
        if alternatives:
            alt_strs = [str(a) if isinstance(a, str) else a.strftime("%m/%d %H:%M") for a in alternatives]
            message = f"{message} 가능한 다른 시간은 {', '.join(alt_strs)} 입니다."

        event = KpiEvent.AGENT_SOFT_FAIL_CLARIFY if recommended_action == "clarify" else KpiEvent.AGENT_HARD_FAIL
        record_kpi_event(event)

        return _build_response_and_record(
            state,
            action=recommended_action,
            message=message,
            department=department,
            ticket=ticket,
            classified_intent=classified_intent,
            safety_result=safety_result,
            intent_result=intent_result,
            policy_result={"reason": message, "allowed": False},
            customer_type=customer_type,
        )

    if action == "book_appointment":
        appointment = {
            "customer_name": effective_customer_name,
            "patient_name": effective_patient_name or effective_customer_name,
            "patient_contact": effective_patient_contact,
            "is_proxy_booking": bool(effective_proxy_booking),
            "birth_date": effective_birth_date,
            "department": department,
            "date": merged_slots.get("date"),
            "time": merged_slots.get("time"),
            "booking_time": booking_time,
            "customer_type": customer_type,
        }
        if is_chat:
            # 채팅 모드: 확인 질문 후 사용자 응답 대기
            if state is not None:
                state["pending_confirmation"] = {"action": "book_appointment", "appointment": appointment}
            _set_pending_missing_info(state, [])
            message = build_confirmation_question(appointment, now)
            record_kpi_event(KpiEvent.AGENT_SOFT_FAIL_CLARIFY)
            return _build_response_and_record(
                state,
                action="clarify",
                message=message,
                department=department,
                ticket=ticket,
                classified_intent=classified_intent,
                safety_result=safety_result,
                intent_result=intent_result,
                policy_result={"allowed": True},
                customer_type=customer_type,
            )
        else:
            # 배치 모드: 확인 없이 즉시 예약 결정
            message = build_confirmation_question(appointment, now)
            record_kpi_event(KpiEvent.AGENT_SUCCESS)
            return _build_response_and_record(
                state,
                action="book_appointment",
                message=message,
                department=department,
                ticket=ticket,
                classified_intent="book_appointment",
                safety_result=safety_result,
                intent_result=intent_result,
                policy_result={"allowed": True},
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
    record_kpi_event(KpiEvent.AGENT_SUCCESS)
    return _build_response_and_record(
        state,
        action=action,
        message=message,
        department=department,
        ticket=ticket,
        classified_intent=classified_intent,
        safety_result=safety_result,
        intent_result=intent_result,
        policy_result={"allowed": True},
        customer_type=customer_type,
    )


def process_message(user_message: str, session: dict | None = None, now: datetime = None) -> dict:
    if now is None:
        now = datetime.now(timezone.utc)

    if session is None:
        session = create_session()

    dialogue_state = session.setdefault("dialogue_state", {})
    dialogue_state.setdefault("customer_name", session.get("customer_name"))
    dialogue_state.setdefault("birth_date", normalize_birth_date(session.get("birth_date")))
    dialogue_state.setdefault("resolved_customer_type", session.get("customer_type"))
    all_appointments = session.get("all_appointments")
    if all_appointments is None:
        all_appointments = _load_appointments_from_disk()
        session["all_appointments"] = all_appointments

    ticket = {
        "message": user_message,
        "customer_name": session.get("customer_name") or dialogue_state.get("customer_name"),
        "birth_date": normalize_birth_date(session.get("birth_date") or dialogue_state.get("birth_date")),
        "customer_type": session.get("customer_type") or dialogue_state.get("resolved_customer_type"),
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
    session["customer_name"] = dialogue_state.get("customer_name") or session.get("customer_name")
    session["patient_name"] = dialogue_state.get("patient_name") or session.get("patient_name")
    session["patient_contact"] = dialogue_state.get("patient_contact") or session.get("patient_contact")
    session["is_proxy_booking"] = dialogue_state.get("is_proxy_booking")
    session["birth_date"] = dialogue_state.get("birth_date") or session.get("birth_date")
    session["customer_type"] = dialogue_state.get("resolved_customer_type") or session.get("customer_type")
    session["last_result"] = result
    return result
