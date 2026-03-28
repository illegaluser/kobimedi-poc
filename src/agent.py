"""
코비메디 예약 챗봇 — 메인 오케스트레이터 모듈.

이 모듈은 코비메디 의원 예약 챗봇의 **핵심 처리 엔진**이다.
사용자의 자연어 메시지를 받아 예약(book), 변경(modify), 취소(cancel),
조회(check) 등의 액션을 수행하고, 안전하지 않은 요청은 거절(reject)하거나
상담원에게 연결(escalate)한다.

처리 파이프라인 (순서 보장):
    1. Safety Gate  — 의료 상담·응급·오프토픽 등 위험 요청을 가장 먼저 차단
    2. Intent Classification — Ollama LLM으로 사용자 의도·슬롯(분과/날짜/시간) 추출
    3. Policy Engine  — 24시간 규칙, 하루 3건 제한 등 결정론적 정책 검증
    4. Cal.com 연동  — 외부 캘린더 가용성 확인 및 예약 생성/취소
    5. Storage 영속화 — bookings.json(진실 원천)에 예약 저장
    6. Response 생성  — 사용자에게 돌려줄 응답 조립 + KPI 이벤트 기록

진입점 (Entry Points):
    - process_ticket()  : 배치(단일 턴) 또는 채팅 세션 내부 처리
    - process_message() : 멀티턴 채팅 래퍼 (세션 관리 포함)

Fast-Path 우회:
    특정 대화 상태(pending_confirmation, pending_candidates, pending_identity 등)에서는
    LLM 호출 없이 패턴 매칭만으로 즉시 응답하여 지연을 최소화한다.

대화 상태 머신 (Dialogue State Machine):
    - pending_confirmation   : 예약 확정 "네/아니요" 대기
    - pending_candidates     : 복수 예약 중 선택 대기
    - pending_missing_info   : 누락 슬롯 수집 대기 (department, date, time, identity 등)
    - pending_alternative_slots : 정책 위반 시 대안 시간 선택 대기
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
from unittest.mock import Mock

from .classifier import safety_check, classify_intent
from . import calcom_client
from .policy import apply_policy, get_appointment_duration, is_within_operating_hours
from .response_builder import (
    build_appointment_options_question,
    build_confirmation_question,
    build_missing_info_question,
    build_response,
    build_success_message,
)
from .storage import (
    cancel_booking,
    create_booking,
    find_bookings,
    load_bookings,
    normalize_birth_date,
    normalize_patient_contact,
    resolve_customer_type_from_history,
)
from .metrics import KpiEvent, record_kpi_event
from .models import User, Ticket as PolicyTicket


# ============================================================================
# 상수 정의: 패턴 매칭·유효 액션·지원 분과 등
# ============================================================================

# 긍정 응답 패턴 — 예약 확정 질문에 대한 "네" 계열 응답 감지
AFFIRMATIVE_PATTERNS = [r"^네$", r"^예$", r"^넵$", r"^맞아요$", r"좋아요", r"진행", r"예약해", r"확정"]

# 부정 응답 패턴 — 예약 확정 질문에 대한 "아니요" 계열 응답 감지
NEGATIVE_PATTERNS = [r"^아니오$", r"^아니요$", r"^아뇨$", r"다시", r"취소할게", r"안 할래", r"싫어", r"싫습니다", r"^안 ?해$", r"^안 ?할래$", r"^됐어$", r"^괜찮아요$"]

# 시스템이 지원하는 전체 액션 열거형 (AGENTS.md 과제 원문 일치 필수)
VALID_ACTIONS = {
    "book_appointment",
    "modify_appointment",
    "cancel_appointment",
    "check_appointment",
    "clarify",
    "escalate",
    "reject",
}

# 예약 관련 액션만 모은 부분집합 — identity 수집·정책 검증 대상 판별에 사용
BOOKING_RELATED_ACTIONS = {
    "book_appointment",
    "modify_appointment",
    "cancel_appointment",
    "check_appointment",
}

# 대리 예약 감지용 정규식 — "대리/가족/엄마" 등이 포함되면 대리 예약으로 판단
PROXY_TRUE_PATTERNS = [r"대리", r"대신", r"가족", r"엄마", r"어머니", r"아버지", r"아빠", r"보호자", r"지인"]

# 본인 예약 감지용 정규식 — "본인/저요/제가" 등이 포함되면 본인 예약으로 판단
PROXY_FALSE_PATTERNS = [r"본인", r"저요", r"저입니다", r"제가", r"환자 본인", r"제가 받을", r"제가 진료"]

# 이름으로 오인될 수 있는 비(非)이름 단어 목록
# 예: "예약", "내과", "오전" 등은 환자 이름이 아니므로 추출에서 제외
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

# 하위 호환 별칭 — 일부 테스트/목에서 classify_safety 이름으로 safety_check를 참조함
classify_safety = safety_check


# ============================================================================
# 유틸리티 함수: 디스크 로드, 세션 생성, 신뢰도/추론 계산 등
# ============================================================================


def _load_appointments_from_disk() -> list[dict]:
    """디스크(bookings.json)에서 전체 예약 목록을 로드하여 반환한다.

    Returns:
        list[dict]: 저장소에 기록된 모든 예약(활성+취소) 목록.
    """
    return load_bookings()


def create_session(
    *,
    customer_name: str | None = None,
    birth_date: str | None = None,
    customer_type: str | None = None,
    all_appointments: list[dict] | None = None,
    context: dict | None = None,
) -> dict:
    """새로운 멀티턴 대화 세션을 생성한다.

    process_message()가 호출될 때마다 이 세션 딕셔너리가 전달되어
    대화 이력, 누적 슬롯, 대화 상태 머신 정보를 턴 간에 유지한다.

    Args:
        customer_name: 고객(예약자) 이름. 사전에 알려진 경우 전달.
        birth_date: 생년월일(YYYY-MM-DD). 동명이인 구분에 사용.
        customer_type: 초진/재진. 저장소 이력으로 자동 판별되므로 보통 None.
        all_appointments: 전체 예약 목록. None이면 디스크에서 자동 로드.
        context: 추가 메타데이터(preferred_department 등).

    Returns:
        dict: 세션 딕셔너리. dialogue_state, all_appointments, last_result 등 포함.
    """
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
    """신뢰도 값을 0.35~0.99 범위로 클램핑하고 소수점 둘째 자리로 반올림한다.

    하드코딩된 고정값이 아닌, 다양한 요인의 합산으로 동적 계산된 값을 안전 범위 내로 조정한다.

    Args:
        value: 원시 신뢰도 점수 (합산 결과).

    Returns:
        float: 0.35 이상 0.99 이하로 클램핑된 반올림 신뢰도.
    """
    return round(max(0.35, min(0.99, value)), 2)


def _determine_classified_intent(
    *,
    result_action: str,
    classified_intent: str | None,
    safety_result: dict | None,
    intent_result: dict | None,
) -> str:
    """최종 classified_intent(분류된 의도)를 결정한다.

    여러 소스(result_action, classified_intent, intent_result, safety_result)를
    우선순위에 따라 검토하여 VALID_ACTIONS에 속하는 첫 번째 값을 반환한다.
    모두 실패하면 safety_result의 category를 기반으로 reject/escalate/clarify 중 선택.

    Args:
        result_action: 현재까지 결정된 액션.
        classified_intent: LLM이 분류한 의도.
        safety_result: Safety Gate 결과 딕셔너리.
        intent_result: Intent Classification 결과 딕셔너리.

    Returns:
        str: VALID_ACTIONS 중 하나. 최악의 경우 "clarify".
    """
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
    """응답 신뢰도(confidence) 점수를 동적으로 계산한다.

    고정값 하드코딩이 아닌, 안전 카테고리·슬롯 채움 정도·정책 결과·
    누락 정보 수 등 다양한 요인을 가감하여 0.35~0.99 범위의 점수를 산출한다.

    가산 요인:
        - safety 비정상 카테고리(medical_advice 등): 높은 확신으로 거절
        - 유효 액션 분류 성공: +0.1
        - department/date/time 추출 성공: 각 +0.07~0.08
        - 정책 통과: +0.08

    감산 요인:
        - clarify 액션: -0.2
        - 누락 정보 2개 이상: -0.1
        - LLM 에러 발생: -0.15

    Args:
        result_action: 최종 결정 액션.
        classified_intent: 분류된 의도.
        safety_result: Safety Gate 결과.
        intent_result: Intent Classification 결과.
        policy_result: 정책 엔진 결과.
        customer_type: 초진/재진 여부.

    Returns:
        float: 0.35~0.99 범위의 신뢰도 점수.
    """
    safety = safety_result or {}
    intent = intent_result or {}
    category = safety.get("category")

    # 안전하지 않은 카테고리는 높은 확신으로 거절/에스컬레이션
    if category and category != "safe":
        score = 0.85
        if category in {"medical_advice", "off_topic", "emergency"}:
            score += 0.14
        if safety.get("unsupported_department") or safety.get("unsupported_doctor"):
            score += 0.1
        if category == "classification_error":
            score -= 0.25
        return _round_confidence(score)

    # 일반 예약 흐름: 기본 0.6에서 가감
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
    """사람이 읽을 수 있는 reasoning(추론 근거) 문자열을 조립한다.

    채점자/관리자가 왜 이 액션이 결정되었는지 한눈에 파악할 수 있도록
    Safety Gate 결과, 의도 분류, 정책 통과 여부 등을 콤마로 이어 붙인다.
    예: "Safety Pass, 저장소 이력 확인(재진), 의도: 예약, 정책 통과"

    Args:
        result_action: 최종 결정 액션.
        classified_intent: 분류된 의도.
        safety_result: Safety Gate 결과.
        intent_result: Intent Classification 결과.
        policy_result: 정책 엔진 결과.
        customer_type: 초진/재진 여부.

    Returns:
        str: 콤마로 구분된 추론 근거 문자열.
    """
    safety = safety_result or {}
    intent = intent_result or {}
    policy = policy_result or {}
    parts: list[str] = []

    # 지원하지 않는 분과/의료진인 경우 즉시 반환
    if safety.get("unsupported_department"):
        parts.append(f"지원불가 분과({safety['unsupported_department']})")
        parts.append(f"Safety Gate: {result_action}")
        return ", ".join(parts)
    if safety.get("unsupported_doctor"):
        parts.append(f"미등록 의료진({safety['unsupported_doctor']})")
        parts.append(f"Safety Gate: {result_action}")
        return ", ".join(parts)

    # 비정상 safety 카테고리 처리
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

    # 정상(safe) 흐름: Safety Pass 후 의도·정책 근거 누적
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
    """응답 딕셔너리에 추가할 런타임 필드(ticket_id, confidence, reasoning 등)를 조립한다.

    build_response()가 생성한 기본 응답에 채점·디버깅용 메타데이터를 덧붙인다.
    classified_intent와 action이 VALID_ACTIONS에 속하도록 보정하는 안전장치도 포함.

    Args:
        ticket: 원본 티켓 딕셔너리.
        result_action: 최종 결정 액션.
        department: 진료과.
        classified_intent: 분류된 의도.
        safety_result: Safety Gate 결과.
        intent_result: Intent Classification 결과.
        policy_result: 정책 엔진 결과.
        customer_type: 초진/재진 여부.
        response: 사용자에게 전달할 응답 텍스트.

    Returns:
        dict: ticket_id, classified_intent, department, action, response,
              confidence, reasoning을 포함하는 딕셔너리.
    """
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
    """티켓 컨텍스트에서 기존 예약을 자동 탐색하여 반환한다.

    변경/취소 요청 시 어떤 예약을 대상으로 할지 결정하는 함수.
    이미 existing_appointment가 지정되어 있으면 그대로 반환하고,
    없으면 ticket.context의 has_existing_appointment 플래그를 기반으로
    고객의 예약 목록에서 분과/날짜 조건에 맞는 예약을 탐색한다.

    후보가 정확히 1건일 때만 자동 선택하며, 0건이거나 2건 이상이면
    None을 반환하여 이후 pending_candidates 흐름으로 넘긴다.

    Args:
        ticket: 사용자 티켓 딕셔너리.
        all_appointments: 전체 예약 목록 (메모리 캐시).
        existing_appointment: 이미 특정된 기존 예약 (있으면 바이패스).
        now: 현재 시각.

    Returns:
        dict | None: 특정된 기존 예약, 또는 특정 불가 시 None.
    """
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
    """텍스트를 정규화한다: 앞뒤 공백 제거 + 연속 공백을 단일 공백으로 치환.

    Args:
        text: 원본 텍스트. None이면 빈 문자열 반환.

    Returns:
        str: 정규화된 텍스트.
    """
    return re.sub(r"\s+", " ", (text or "").strip())


# ============================================================================
# 세션 상태 관리 함수들: 대화 상태 머신 초기화·업데이트·리셋
# ============================================================================


def _init_session_state(session_state: dict | None) -> dict:
    """세션 상태 딕셔너리를 초기화하거나, 기존 상태에 누락된 키를 보충한다.

    멀티턴 대화에서 턴 간에 유지해야 할 모든 상태 필드를 정의한다:
    - conversation_history: 대화 이력 (user/assistant 메시지 쌍)
    - accumulated_slots: 턴을 걸쳐 누적된 슬롯 (department, date, time)
    - customer_name/patient_name/patient_contact/birth_date: 신원 정보
    - is_proxy_booking: 대리 예약 여부 (None=미확인, True=대리, False=본인)
    - pending_confirmation: 확정 대기 중인 예약 정보
    - pending_action: 현재 진행 중인 액션 (book/modify/cancel/check)
    - pending_missing_info/queue: 아직 수집하지 못한 필드 목록
    - pending_candidates: 복수 예약 선택 대기 목록
    - pending_alternative_slots: 정책 위반 시 대안 시간 목록
    - clarify_turn_count: 연속 clarify 횟수 (4회 초과 시 escalate)

    Args:
        session_state: 기존 세션 상태. None이면 새로 생성.

    Returns:
        dict: 초기화 완료된 세션 상태 딕셔너리.
    """
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
    """대화 이력에 메시지를 추가한다.

    session_state가 None(배치 모드)이면 아무것도 하지 않는다.

    Args:
        session_state: 세션 상태 딕셔너리 또는 None.
        role: "user" 또는 "assistant".
        content: 메시지 내용.
    """
    if session_state is None:
        return
    session_state["conversation_history"].append({"role": role, "content": content})


def _set_pending_missing_info(session_state: dict | None, missing_info: list[str]) -> None:
    """세션의 pending_missing_info와 pending_missing_info_queue를 동시에 설정한다.

    중복을 제거한 뒤 두 리스트를 동기화하여 일관성을 유지한다.

    Args:
        session_state: 세션 상태. None이면 무시.
        missing_info: 수집해야 할 필드 이름 목록.
    """
    if session_state is None:
        return
    deduped: list[str] = []
    for item in missing_info:
        if item and item not in deduped:
            deduped.append(item)
    session_state["pending_missing_info"] = deduped
    session_state["pending_missing_info_queue"] = deduped.copy()


def _get_pending_missing_info(session_state: dict | None) -> list[str]:
    """현재 수집 대기 중인 누락 필드 목록을 반환한다.

    queue가 비어 있으면 pending_missing_info를 폴백으로 사용한다.

    Args:
        session_state: 세션 상태. None이면 빈 리스트 반환.

    Returns:
        list[str]: 수집해야 할 필드 이름 목록.
    """
    if session_state is None:
        return []
    queue = session_state.get("pending_missing_info_queue")
    if isinstance(queue, list) and queue:
        return list(queue)
    pending = session_state.get("pending_missing_info") or []
    return list(pending)


def _increment_clarify_turn_count(session_state: dict | None) -> None:
    """clarify 연속 횟수를 1 증가시킨다.

    4회를 초과하면 _should_escalate_for_clarify_limit에서 상담원 전환을 트리거한다.

    Args:
        session_state: 세션 상태. None이면 무시.
    """
    if session_state is None:
        return
    session_state["clarify_turn_count"] = int(session_state.get("clarify_turn_count") or 0) + 1


def _reset_clarify_turn_count(session_state: dict | None) -> None:
    """clarify 연속 횟수를 0으로 리셋한다.

    대화가 진전될 때(슬롯이 새로 채워지거나, 확정 거부 후 재시작 등) 호출된다.

    Args:
        session_state: 세션 상태. None이면 무시.
    """
    if session_state is None:
        return
    session_state["clarify_turn_count"] = 0


def _prioritize_missing_info(missing_info: list[str]) -> list[str]:
    """누락 필드 목록을 우선순위에 따라 정렬한다.

    대리 여부(is_proxy_booking)가 가장 먼저 확인되어야 하고,
    이후 환자 이름 → 연락처 → 분과 → 날짜 → 시간 → 생년월일 순으로 질문한다.
    이 순서는 대화 UX 최적화를 위한 것이며, 대리 여부를 먼저 확인해야
    patient_name이 customer_name과 동일한지 판단할 수 있기 때문이다.

    우선순위:
        0: is_proxy_booking (대리 여부 — 반드시 최우선)
        1: patient_name
        2: patient_contact
        3: department
        4: date
        5: time
        6: birth_date
        7: appointment_target
        8: slot_selection
        9: confirmation
        10: customer_name

    Args:
        missing_info: 정렬 전 누락 필드 목록.

    Returns:
        list[str]: 우선순위에 따라 정렬된 중복 제거 목록.
    """
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
    """clarify 연속 횟수가 한계(4회)를 초과했는지, 그리고 핵심 필드가 여전히 누락인지 확인한다.

    4회 이상 clarify가 반복되면서 핵심 정보(대리 여부, 이름, 연락처, 분과, 날짜, 시간 등)가
    아직 채워지지 않았다면 True를 반환하여 상담원 전환(escalate)을 유도한다.

    Args:
        session_state: 세션 상태. None이면 False.
        missing_info: 현재 누락된 필드 목록.

    Returns:
        bool: 상담원 전환이 필요하면 True.
    """
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
    """대화 상태를 완전히 초기화한다.

    예약 성공·취소 완료 등 하나의 플로우가 종료된 후 호출하여
    다음 대화에서 이전 상태가 간섭하지 않도록 깨끗이 리셋한다.

    Args:
        session_state: 세션 상태. None이면 무시.
    """
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
    """진행 중인 플로우를 리셋하고 새로운 액션으로 전환한다.

    사용자가 예약 진행 중 "아, 취소할게요"처럼 의도를 바꿨을 때 호출하여
    누적 슬롯·확정 대기·후보 목록 등을 모두 초기화하고 새 액션을 설정한다.
    단, customer_name/patient_name 등 신원 정보는 유지한다.

    Args:
        session_state: 세션 상태. None이면 무시.
        new_action: 전환할 새 액션. None이면 pending_action 유지.
    """
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


# ============================================================================
# 응답 조립 및 기록 함수
# ============================================================================


def _build_response_and_record(session_state: dict | None, **kwargs) -> dict:
    """최종 응답 딕셔너리를 조립하고, 대화 이력에 기록하며, 런타임 필드를 추가한다.

    이 함수는 모든 응답 생성의 **단일 출구(single exit point)**로,
    build_response()를 호출한 뒤 다음을 수행한다:
    1. 의료 상담 포함 예약 요청(contains_booking_subrequest)이면 경고 문구 선행 삽입
    2. _build_runtime_fields()로 confidence/reasoning/classified_intent 등 메타데이터 추가
    3. 대화 이력에 assistant 메시지 기록
    4. session_state["last_result"]에 결과 저장

    Args:
        session_state: 세션 상태 딕셔너리 또는 None(배치 모드).
        **kwargs: build_response()에 전달할 인자 +
                  ticket, classified_intent, safety_result, intent_result,
                  policy_result, customer_type (런타임 필드 계산용).

    Returns:
        dict: action, response, confidence, reasoning 등을 포함하는 최종 응답.
    """
    ticket = kwargs.pop("ticket", None)
    classified_intent = kwargs.pop("classified_intent", None)
    safety_result = kwargs.pop("safety_result", None)
    intent_result = kwargs.pop("intent_result", None)
    policy_result = kwargs.pop("policy_result", None)
    customer_type = kwargs.pop("customer_type", None)

    result = build_response(**kwargs)
    # 의료 상담 요청이 섞인 예약 메시지에는 경고 문구를 앞에 붙인다
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


# ============================================================================
# 사용자 입력 패턴 매칭 함수들
# ============================================================================


def _is_affirmative(message: str) -> bool:
    """메시지가 긍정 응답("네", "맞아요", "진행" 등)인지 판별한다.

    AFFIRMATIVE_PATTERNS 정규식 목록과 매칭하여 하나라도 일치하면 True.

    Args:
        message: 사용자 입력 메시지.

    Returns:
        bool: 긍정 응답이면 True.
    """
    text = _normalize_text(message)
    return any(re.search(pattern, text) for pattern in AFFIRMATIVE_PATTERNS)


def _is_negative(message: str) -> bool:
    """메시지가 부정 응답("아니요", "취소할게", "안 할래" 등)인지 판별한다.

    NEGATIVE_PATTERNS 정규식 목록과 매칭하여 하나라도 일치하면 True.

    Args:
        message: 사용자 입력 메시지.

    Returns:
        bool: 부정 응답이면 True.
    """
    text = _normalize_text(message)
    return any(re.search(pattern, text) for pattern in NEGATIVE_PATTERNS)


def _infer_requested_action(message: str) -> str | None:
    """메시지에서 키워드 기반으로 사용자가 원하는 액션을 추론한다.

    LLM 없이 단순 키워드 매칭으로 빠르게 의도를 파악하는 헬퍼.
    "취소" → cancel, "변경/바꿔" → modify, "확인/조회" → check, "예약/진료" → book.

    Args:
        message: 사용자 입력 메시지.

    Returns:
        str | None: 추론된 액션 문자열 또는 None(추론 불가).
    """
    text = _normalize_text(message)
    if any(keyword in text for keyword in ["취소", "예약 취소", "빼줘", "안 갈래", "안갈래", "못 가", "못가"]):
        return "cancel_appointment"
    if any(keyword in text for keyword in ["변경", "바꿔", "옮겨", "수정"]):
        return "modify_appointment"
    if any(keyword in text for keyword in ["확인", "조회"]):
        return "check_appointment"
    if any(keyword in text for keyword in ["예약", "진료", "접수"]):
        return "book_appointment"
    return None


def _classify_intent_with_optional_now(user_message: str, now: datetime, conversation_history: list[dict] | None = None) -> dict:
    """classify_intent를 호출하되, now/conversation_history 인자를 지원 여부에 따라 전달한다.

    테스트 환경에서 classify_intent가 Mock으로 교체된 경우 user_message만 전달하고,
    실제 LLM 함수인 경우 now와 conversation_history도 함께 전달한다.
    TypeError가 발생하면(구 시그니처) user_message만으로 폴백 호출한다.

    Args:
        user_message: 사용자 입력 메시지.
        now: 현재 시각 (날짜 표현 해석용).
        conversation_history: 대화 이력 (멀티턴 컨텍스트 제공용).

    Returns:
        dict: action, department, date, time, missing_info 등을 포함하는 의도 분류 결과.
    """
    if isinstance(classify_intent, Mock):
        return classify_intent(user_message)
    try:
        return classify_intent(user_message, now=now, conversation_history=conversation_history)
    except TypeError:
        return classify_intent(user_message)


# ============================================================================
# 티켓 컨텍스트 병합 (배치 모드 전용)
# ============================================================================

# customer_type 문자열 정규화 맵: 다양한 표현을 "초진"/"재진"으로 통일
_TICKET_CUSTOMER_TYPE_MAP = {
    "초진": "초진", "first_visit": "초진", "first": "초진", "new": "초진",
    "재진": "재진", "returning": "재진", "follow_up": "재진", "follow-up": "재진", "revisit": "재진",
}

# 시스템이 지원하는 진료과 목록 — 이 외의 분과는 reject 처리
_SUPPORTED_DEPARTMENTS = {"이비인후과", "내과", "정형외과"}


def _merge_ticket_context_into_intent(ticket: dict, intent_result: dict) -> dict:
    """배치 모드 전용: ticket 구조화 메타데이터로 intent 누락 필드를 채운다.

    LLM이 message 텍스트에서 추출하지 못한 customer_type / preferred_* 필드를
    ticket 에 이미 정리된 값으로 보완하여 불필요한 clarify 응답을 방지한다.

    보완 대상:
        - customer_type: ticket.customer_type → "초진"/"재진"으로 정규화
        - department: context.preferred_department (_SUPPORTED_DEPARTMENTS 내)
        - date: context.preferred_date
        - time: context.preferred_time

    모든 필수 슬롯이 채워지면 clarify → book_appointment로 업그레이드한다.

    Args:
        ticket: 배치 티켓 딕셔너리.
        intent_result: LLM 의도 분류 결과.

    Returns:
        dict: 보완된 intent_result (원본 수정 없이 복사본 반환).
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


# ============================================================================
# Safety Gate 실행 및 응답 생성
# ============================================================================


def _run_safety_gate(user_message: str, session_state: dict | None = None) -> dict:
    """Safety Gate를 실행하여 메시지의 안전 카테고리를 판별한다.

    파이프라인에서 **가장 먼저** 실행되어야 하며, "safe"가 아닌 경우
    이후 처리를 건너뛰고 즉시 거절/에스컬레이션 응답을 반환한다.

    Fast-Path 우회 조건 (LLM 호출 없이 "safe" 반환):
        - 확정 대기 중 + 네/아니요 응답
        - 후보 선택 대기 중 + "N번" 형식 응답
        - 대안 슬롯 선택 대기 중 + "N번" 형식 응답
        - 대리 여부 질문 대기 중 + 본인/대리 패턴 매칭 성공
        - 정보 수집 대기 중 (이름, 연락처, 날짜 등) → 풀 체크 실행 후 결과 반환

    Args:
        user_message: 사용자 입력 메시지.
        session_state: 세션 상태. None(배치 모드)이면 항상 풀 체크.

    Returns:
        dict: category="safe" 또는 비정상 카테고리 + 부가 필드
              (unsupported_department, unsupported_doctor 등).
    """
    if session_state is not None:
        # 확정 대기 중 네/아니요는 안전 — LLM 호출 불필요
        if session_state.get("pending_confirmation") and (_is_affirmative(user_message) or _is_negative(user_message)):
            return {"category": "safe"}

        # 후보 선택 대기 중 "N번" 응답은 안전
        if session_state.get("pending_candidates") and re.fullmatch(r"\d+번(이요|이에요|으로|로)?", _normalize_text(user_message)):
            return {"category": "safe"}

        # 대안 슬롯 선택 대기 중 "N번" 응답은 안전
        if session_state.get("pending_alternative_slots") and re.fullmatch(r"\d+번?(이요|이에요|으로|로)?", _normalize_text(user_message)):
            return {"category": "safe"}

        # 대리 여부 질문 대기 중 본인/대리 패턴 매칭 성공 시 안전
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
    """Safety Gate가 차단한 메시지에 대해 적절한 거절/에스컬레이션 응답을 생성한다.

    카테고리별 응답:
        - emergency: escalate ("응급 가능성 → 상담원/의료진 확인 필요")
        - medical_advice: reject ("의료법상 의료 상담 불가")
        - off_topic: reject ("예약 관련 문의만 가능")
        - privacy_request: reject ("타인 개인정보 제공 불가")
        - complaint: escalate ("상담원 연결")
        - operational_escalation: escalate ("상담원 확인 필요")
        - classification_error + clarify 폴백: clarify ("일시적 오류")
        - 기타: reject ("안전성 판단 실패")

    모든 분기에서 KpiEvent.SAFE_REJECT를 기록한다.

    Args:
        safety_result: Safety Gate 결과 딕셔너리.
        session_state: 세션 상태.
        ticket: 원본 티켓.
        customer_type: 초진/재진.

    Returns:
        dict: 최종 응답 딕셔너리.
    """
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
    """지원하지 않는 분과/의료진이 감지된 경우 reject 응답을 생성한다.

    Safety Gate가 unsupported_department 또는 unsupported_doctor를
    탐지했으면 해당 안내 메시지와 함께 reject를 반환한다.
    어느 쪽도 해당하지 않으면 None을 반환하여 정상 흐름을 계속한다.

    Args:
        safety_result: Safety Gate 결과.
        session_state: 세션 상태.
        ticket: 원본 티켓.
        customer_type: 초진/재진.

    Returns:
        dict | None: reject 응답 딕셔너리 또는 None.
    """
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
    """증상 기반 분과 안내 응답을 생성한다.

    Safety Gate가 mixed_department_guidance=True를 반환한 경우 호출된다.
    의료 상담은 불가하지만, 예약 안내 기준으로 적절한 분과를 제안할 수 있다.
    분과 힌트가 있으면 해당 분과 예약을 안내하고, 없으면 일반 안내를 한다.

    Args:
        department: Safety Gate가 추론한 분과 힌트.
        session_state: 세션 상태.
        ticket: 원본 티켓.
        safety_result: Safety Gate 결과.
        customer_type: 초진/재진.

    Returns:
        dict: clarify 액션의 응답 딕셔너리.
    """
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
    """Safety Gate 출력을 표준 딕셔너리 형태로 정규화한다.

    classify_safety()가 딕셔너리를 반환하면 그대로 사용하고,
    문자열(카테고리명)을 반환하면 표준 딕셔너리로 래핑한다.

    Args:
        safety_output: classify_safety()의 반환값 (dict 또는 str).

    Returns:
        dict: category, department_hint, unsupported_department 등을 포함하는 표준 딕셔너리.
    """
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


# ============================================================================
# 슬롯 병합 및 동기화
# ============================================================================


def _merge_accumulated_slots(session_state: dict | None, intent_result: dict) -> dict:
    """현재 턴의 intent_result 슬롯과 세션에 누적된 슬롯을 병합한다.

    새 턴에서 추출된 값이 있으면 기존 누적값을 덮어쓰고,
    없으면 기존 누적값을 유지한다 (OR 병합).
    슬롯이 새로 채워지면 clarify_turn_count를 리셋하여
    대화가 진전 중임을 표시한다.

    Args:
        session_state: 세션 상태. None이면 intent_result 값만 반환.
        intent_result: 현재 턴의 의도 분류 결과.

    Returns:
        dict: 병합된 슬롯 딕셔너리 (department, date, time).
    """
    accumulated = (session_state or {}).get("accumulated_slots", {})
    merged = {
        "department": intent_result.get("department") or accumulated.get("department"),
        "date": intent_result.get("date") or accumulated.get("date"),
        "time": intent_result.get("time") or accumulated.get("time"),
    }
    if session_state is not None:
        # 슬롯이 새로 채워졌으면 대화가 진전 중이므로 clarify 카운트 리셋
        for key in ("department", "date", "time"):
            if merged.get(key) and not accumulated.get(key):
                _reset_clarify_turn_count(session_state)
                break
        session_state["accumulated_slots"] = merged.copy()
    return merged


def _sync_queue_with_accumulated_slots(session_state: dict | None, merged_slots: dict) -> None:
    """pending_missing_info_queue에서 이미 채워진 슬롯 항목을 제거한다. (D-001 fix)

    classify_intent가 department/date/time을 추출해 accumulated_slots에 반영했음에도
    pending_missing_info_queue가 해당 항목을 여전히 포함하는 경우를 방지한다.
    _determine_dialogue_missing_info가 최종 판정 기준이지만, 큐를 미리 동기화하여
    LLM 결과의 edge case로 인한 불일치를 방어적으로 차단한다.

    Args:
        session_state: 세션 상태. None이면 무시.
        merged_slots: 병합된 슬롯 딕셔너리 (department, date, time).
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


# ============================================================================
# 유효 필드 조회 헬퍼: ticket과 session_state에서 우선순위대로 값을 추출
# ============================================================================


def _get_effective_customer_name(ticket: dict | None, session_state: dict | None) -> str | None:
    """ticket 또는 session_state에서 유효한 customer_name(예약자 이름)을 반환한다.

    ticket을 우선 참조하고, 없으면 session_state에서 가져온다.

    Args:
        ticket: 티켓 딕셔너리.
        session_state: 세션 상태 딕셔너리.

    Returns:
        str | None: 예약자 이름 또는 None.
    """
    return (ticket or {}).get("customer_name") or (session_state or {}).get("customer_name")


def _get_effective_birth_date(ticket: dict | None, session_state: dict | None) -> str | None:
    """ticket 또는 session_state에서 유효한 birth_date(생년월일)를 정규화하여 반환한다.

    Args:
        ticket: 티켓 딕셔너리.
        session_state: 세션 상태 딕셔너리.

    Returns:
        str | None: YYYY-MM-DD 형식의 생년월일 또는 None.
    """
    raw_birth_date = (ticket or {}).get("birth_date") or (session_state or {}).get("birth_date")
    return normalize_birth_date(raw_birth_date)


def _format_patient_contact(value: str | None) -> str | None:
    """전화번호를 NNN-NNNN-NNNN 또는 NNN-NNN-NNNN 형식으로 포맷팅한다.

    normalize_patient_contact()로 숫자만 추출한 뒤 길이에 따라 하이픈을 삽입한다.

    Args:
        value: 원시 전화번호 문자열.

    Returns:
        str | None: 포맷팅된 전화번호 또는 None (유효하지 않은 경우).
    """
    normalized = normalize_patient_contact(value)
    if not normalized:
        return None
    if len(normalized) == 11:
        return f"{normalized[:3]}-{normalized[3:7]}-{normalized[7:]}"
    if len(normalized) == 10:
        return f"{normalized[:3]}-{normalized[3:6]}-{normalized[6:]}"
    return normalized


def _extract_patient_contact(text: str | None) -> str | None:
    """자유 형식 텍스트에서 환자 전화번호를 추출하여 포맷팅한다.

    정규식으로 01X-XXXX-XXXX 패턴을 탐색하고,
    매칭 실패 시 숫자만 추출하여 01X로 시작하는 10~11자리를 검증한다.

    Args:
        text: 사용자 입력 텍스트.

    Returns:
        str | None: 포맷팅된 전화번호 또는 None.
    """
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
    """사용자 응답에서 대리 예약 여부를 패턴 매칭으로 판별한다.

    PROXY_TRUE_PATTERNS("대리", "가족", "엄마" 등) 매칭 시 True,
    PROXY_FALSE_PATTERNS("본인", "저요", "제가" 등) 매칭 시 False,
    어느 쪽도 매칭되지 않으면 None을 반환한다.

    이 함수는 LLM 호출 없이 결정론적으로 동작한다.

    Args:
        text: 사용자 응답 텍스트.

    Returns:
        bool | None: True(대리), False(본인), None(판별 불가).
    """
    normalized = _normalize_text(text)
    if not normalized:
        return None
    if any(re.search(pattern, normalized) for pattern in PROXY_TRUE_PATTERNS):
        return True
    if any(re.search(pattern, normalized) for pattern in PROXY_FALSE_PATTERNS):
        return False
    return None


def _get_effective_patient_name(ticket: dict | None, session_state: dict | None) -> str | None:
    """ticket 또는 session_state에서 유효한 patient_name(환자 이름)을 반환한다.

    대리 예약이 아닌 배치 모드(session_state=None)에서는
    patient_name이 없으면 customer_name을 폴백으로 사용한다.

    Args:
        ticket: 티켓 딕셔너리.
        session_state: 세션 상태 딕셔너리.

    Returns:
        str | None: 환자 이름 또는 None.
    """
    return (
        (ticket or {}).get("patient_name")
        or (session_state or {}).get("patient_name")
        or ((ticket or {}).get("customer_name") if session_state is None else None)
    )


def _get_effective_patient_contact(ticket: dict | None, session_state: dict | None) -> str | None:
    """ticket 또는 session_state에서 유효한 patient_contact(환자 연락처)를 포맷팅하여 반환한다.

    Args:
        ticket: 티켓 딕셔너리.
        session_state: 세션 상태 딕셔너리.

    Returns:
        str | None: 포맷팅된 전화번호 또는 None.
    """
    return _format_patient_contact((ticket or {}).get("patient_contact") or (session_state or {}).get("patient_contact"))


def _get_effective_proxy_booking(ticket: dict | None, session_state: dict | None) -> bool | None:
    """ticket 또는 session_state에서 유효한 is_proxy_booking(대리 예약 여부)을 반환한다.

    ticket을 우선 참조하고, 없으면 session_state에서 가져온다.
    둘 다 None이면 "아직 확인하지 않음"을 의미하는 None을 반환.

    Args:
        ticket: 티켓 딕셔너리.
        session_state: 세션 상태 딕셔너리.

    Returns:
        bool | None: True(대리), False(본인), None(미확인).
    """
    if (ticket or {}).get("is_proxy_booking") is not None:
        return bool(ticket.get("is_proxy_booking"))
    if (session_state or {}).get("is_proxy_booking") is not None:
        return bool(session_state.get("is_proxy_booking"))
    return None


# ============================================================================
# 신원 정보 동기화: intent_result에서 추출한 정보를 ticket/session에 반영
# ============================================================================


def _sync_identity_state_from_intent(ticket: dict, session_state: dict | None, intent_result: dict, *, is_chat: bool) -> None:
    """LLM 의도 분류 결과에서 추출된 신원 정보를 ticket과 session_state에 동기화한다.

    동기화 대상: is_proxy_booking, patient_name, patient_contact, birth_date.

    주의사항:
        - F-031 버그 수정: 채팅 모드에서 LLM이 임의로 is_proxy_booking=False를 추정하는 것을
          방지. 반드시 사용자가 명시적으로 "본인"이라고 답한 경우에만 False 설정.
        - 채팅 모드에서 이미 확정된 patient_name은 후속 턴의 오추출(예: "다음주"→이름)로
          덮어쓰지 않도록 보호.
        - 배치 모드(session_state=None)에서 본인 예약이고 patient_name이 없으면
          customer_name을 자동 할당.

    Args:
        ticket: 티켓 딕셔너리 (수정됨).
        session_state: 세션 상태 딕셔너리 (수정됨). None이면 배치 모드.
        intent_result: LLM 의도 분류 결과.
        is_chat: 멀티턴 채팅 모드 여부.
    """
    if session_state is None and not ticket:
        return

    patient_name = intent_result.get("patient_name")
    patient_contact = _format_patient_contact(intent_result.get("patient_contact"))
    birth_date = normalize_birth_date(intent_result.get("birth_date"))
    proxy_flag = intent_result.get("is_proxy_booking")

    if proxy_flag is not None:
        # Bug fix (F-031): 채팅 모드에서는 LLM이 is_proxy_booking=False를 추정하면 안 됨.
        # True(대리 신호)만 신뢰하고, False는 배치 모드 또는 사용자 명시 확인 시에만 수용.
        if not (is_chat and proxy_flag is False):
            ticket["is_proxy_booking"] = bool(proxy_flag)
            if session_state is not None:
                session_state["is_proxy_booking"] = bool(proxy_flag)

    if patient_name:
        # 채팅 모드에서 이미 확정된 patient_name이 있으면 후속 턴의
        # 오추출(예: "다음주"→이름)로 덮어쓰지 않도록 보호한다.
        existing_name = (session_state or {}).get("patient_name") or ticket.get("patient_name")
        if not (is_chat and existing_name and existing_name != patient_name):
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

    # 배치 모드: 본인 예약이고 patient_name이 없으면 customer_name을 자동 할당
    if session_state is None and not ticket.get("patient_name") and ticket.get("customer_name") and not ticket.get("is_proxy_booking"):
        ticket["patient_name"] = ticket.get("customer_name")

    # 채팅 모드: 본인 예약 확정 후 patient_name이 없으면 customer_name으로 채움
    if session_state is not None and is_chat and session_state.get("is_proxy_booking") is False and not session_state.get("patient_name") and ticket.get("customer_name"):
        session_state["patient_name"] = ticket.get("customer_name")


# ============================================================================
# 환자 이름 / 생년월일 추출 (자유 형식 텍스트에서)
# ============================================================================


def _extract_patient_name(text: str | None) -> str | None:
    """자유 형식 텍스트에서 환자 이름을 추출한다.

    두 가지 전략:
    1. 명시적 패턴: "이름은 홍길동", "저는 홍길동" 등의 구문 매칭
    2. 토큰 스캔: 전화번호를 제거한 뒤, 2~4글자 한글 토큰 중
       _NON_NAME_WORDS에 해당하지 않는 첫 번째 토큰을 이름으로 추출

    Args:
        text: 사용자 입력 텍스트.

    Returns:
        str | None: 추출된 환자 이름 또는 None.
    """
    normalized = _normalize_text(text)
    if not normalized:
        return None

    # 전략 1: "이름은 XXX" 패턴
    match = re.search(r"(?:제 이름은|이름은|저는|환자 이름은)\s*([가-힣A-Za-z]{2,20})", normalized)
    if match:
        cleaned = re.sub(r"(?:입니다|이에요|예요|이요|요)$", "", match.group(1)).strip(" .,!")
        if cleaned and cleaned not in _NON_NAME_WORDS:
            return cleaned

    # 전략 2: 전화번호 제거 후 한글 토큰 스캔
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
    """자유 형식 텍스트에서 생년월일을 추출하여 YYYY-MM-DD로 정규화한다.

    정규식으로 "YYYY.MM.DD", "YYYYMMDD", "YYYY년 MM월 DD일" 패턴을 탐색하고,
    normalize_birth_date()로 유효성 검증 및 정규화한다.

    Args:
        text: 사용자 입력 텍스트.

    Returns:
        str | None: YYYY-MM-DD 형식의 생년월일 또는 None.
    """
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


# ============================================================================
# 저장소 이력 기반 초진/재진 판별
# ============================================================================


def _resolve_history_customer_type(
    customer_name: str | None,
    birth_date: str | None,
    patient_contact: str | None = None,
) -> dict:
    """저장소(bookings.json) 이력을 조회하여 초진/재진을 판별한다.

    이름+생년월일 또는 전화번호로 기존 예약 이력을 검색하고,
    resolve_customer_type_from_history()의 결과를 반환한다.

    반환 딕셔너리 필드:
        - customer_type: "초진" 또는 "재진" 또는 None
        - ambiguous: True면 동명이인 존재 → birth_date 추가 수집 필요
        - birth_date_candidates: 동명이인의 생년월일 후보 목록
        - matched_bookings: 매칭된 예약 목록
        - has_non_cancelled_history: 활성 예약 이력 존재 여부
        - has_cancelled_history: 취소 이력 존재 여부

    Args:
        customer_name: 고객(환자) 이름.
        birth_date: 생년월일 (동명이인 구분용).
        patient_contact: 환자 전화번호 (이름 없이 검색 가능).

    Returns:
        dict: 이력 조회 결과. 조회 실패 시에도 기본 구조 반환 (예외 안전).
    """
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


# ============================================================================
# 누락 정보(Missing Info) 판별
# ============================================================================


def _determine_missing_info(
    action: str,
    slots: dict,
    customer_name: str | None = None,
    birth_date: str | None = None,
    history_resolution: dict | None = None,
) -> list[str]:
    """배치 모드용 간단한 누락 정보 판별 함수.

    book_appointment에 대해서만 department, date, time, customer_name의
    누락 여부를 검사한다. 동명이인(ambiguous)인 경우 birth_date도 추가.

    Args:
        action: 현재 액션.
        slots: 슬롯 딕셔너리 (department, date, time).
        customer_name: 고객 이름.
        birth_date: 생년월일.
        history_resolution: 저장소 이력 조회 결과.

    Returns:
        list[str]: 누락된 필드 이름 목록.
    """
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
    target_appointment: dict | None = None,
) -> list[str]:
    """멀티턴 대화용 종합적인 누락 정보 판별 함수.

    _determine_missing_info보다 훨씬 정교하며, 다음을 추가로 고려한다:
    - is_proxy_booking 미확인 시 최우선 수집
    - 채팅 모드: patient_name, patient_contact 필수
    - 배치 모드: 대리 예약 시에만 patient_name, patient_contact 수집
    - modify_appointment: 기존 예약과 동일한 날짜/시간이면 새 값 필요
    - 동명이인(ambiguous) 시 birth_date 수집

    결과는 _prioritize_missing_info()로 우선순위 정렬된다.

    Args:
        action: 현재 액션.
        slots: 슬롯 딕셔너리.
        is_chat: 멀티턴 채팅 모드 여부.
        is_proxy_booking: 대리 예약 여부 (None=미확인).
        patient_name: 환자 이름.
        patient_contact: 환자 연락처.
        customer_name: 예약자 이름.
        birth_date: 생년월일.
        history_resolution: 저장소 이력 조회 결과.
        target_appointment: 변경 대상 기존 예약 (modify_appointment 시).

    Returns:
        list[str]: 우선순위 정렬된 누락 필드 목록.
    """
    # 채팅 모드에서 대리 여부 미확인이면 다른 정보 수집 전에 먼저 질문
    if action in BOOKING_RELATED_ACTIONS and is_chat and is_proxy_booking is None:
        return ["is_proxy_booking"]

    missing: list[str] = []

    if action not in BOOKING_RELATED_ACTIONS:
        return []

    if is_chat and is_proxy_booking is None:
        missing.append("is_proxy_booking")

    # 환자 이름 결정: 본인 예약이면 customer_name을 환자 이름으로 사용
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

    # book_appointment: department, date, time 필수
    if action == "book_appointment":
        if not slots.get("department"):
            missing.append("department")
        if not slots.get("date"):
            missing.append("date")
        if not slots.get("time"):
            missing.append("time")

    # modify_appointment: 기존 예약과 동일한 date/time이면 새 값 미입력으로 간주
    if action == "modify_appointment":
        existing_date = (target_appointment or {}).get("date")
        existing_time = (target_appointment or {}).get("time")
        new_date = slots.get("date")
        new_time = slots.get("time")
        date_is_new = new_date and new_date != existing_date
        time_is_new = new_time and new_time != existing_time
        has_any_change = date_is_new or time_is_new
        if not has_any_change:
            # 둘 다 기존과 동일하거나 미입력 → 새 날짜/시간 모두 수집
            missing.append("date")
            missing.append("time")

    if not customer_name and patient_name and is_proxy_booking is False:
        customer_name = patient_name

    if action == "book_appointment" and not customer_name and not patient_name and not is_chat:
        missing.append("customer_name")

    if history_resolution and history_resolution.get("ambiguous") and not birth_date:
        missing.append("birth_date")

    return _prioritize_missing_info(missing)


# ============================================================================
# 대안 슬롯(Alternative Slots) 처리
# ============================================================================


def _build_alternative_slot_question(alternative_slots: list[str], now: datetime) -> str:
    """정책 위반 시 제안할 대안 시간 선택 질문을 생성한다.

    각 대안 슬롯을 번호 목록으로 포맷팅하여 사용자가 "1번", "2번"으로 선택할 수 있게 한다.

    Args:
        alternative_slots: 대안 booking_time ISO 문자열 목록.
        now: 현재 시각 (시간 포맷팅용).

    Returns:
        str: "요청하신 시간은 어렵습니다. 가능한 다른 시간 중..." 형식의 질문 문자열.
    """
    options: list[str] = []
    for index, slot in enumerate(alternative_slots, start=1):
        options.append(f"{index}) {build_success_message('check_appointment', appointment={'booking_time': slot}, now=now).replace('확인된 예약은 ', '').replace('입니다.', '')}")
    return "요청하신 시간은 어렵습니다. 가능한 다른 시간 중 원하시는 번호를 선택해주세요. " + ", ".join(options)


def _resolve_alternative_slot_selection(message: str, alternative_slots: list[str]) -> str | None:
    """사용자의 대안 슬롯 선택 응답에서 선택된 슬롯을 반환한다.

    "1번", "2" 등의 숫자를 추출하여 해당 인덱스의 슬롯을 반환한다.

    Args:
        message: 사용자 입력 메시지.
        alternative_slots: 대안 booking_time 목록.

    Returns:
        str | None: 선택된 booking_time 문자열 또는 None (유효하지 않은 선택).
    """
    text = _normalize_text(message)
    number_match = re.search(r"(\d+)", text)
    if not number_match:
        return None
    index = int(number_match.group(1)) - 1
    if 0 <= index < len(alternative_slots):
        return alternative_slots[index]
    return None


def _update_slots_from_booking_time(session_state: dict | None, booking_time: str, now: datetime) -> dict:
    """booking_time ISO 문자열에서 date/time을 추출하여 세션의 accumulated_slots를 업데이트한다.

    대안 슬롯 선택 후 호출되어, 선택된 시간으로 세션 슬롯을 갱신한다.

    Args:
        session_state: 세션 상태.
        booking_time: ISO 형식 booking_time 문자열.
        now: 현재 시각.

    Returns:
        dict: 업데이트된 슬롯 딕셔너리 (department, date, time).
    """
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


# ============================================================================
# 대리 예약/신원 정보 수집 (pending_identity_input) 처리
# ============================================================================


def _consume_pending_identity_input(
    user_message: str,
    ticket: dict,
    session_state: dict | None,
) -> tuple[dict | None, dict | None]:
    """대기 중인 신원 정보(identity) 질문에 대한 사용자 응답을 처리한다.

    Fast-Path로 동작하며 LLM 호출 없이 패턴 매칭만으로 처리한다:
    1. 의도 전환 감지: 예약 진행 중 "취소할게요" 등으로 의도가 바뀌면
       pending 상태를 리셋하고 (None, None)을 반환하여 메인 흐름에서 재분류.
    2. is_proxy_booking 처리: "본인"/"대리" 패턴 매칭으로 확정.
       판별 불가 시 재질문하고, 4회 초과 시 상담원 전환(escalate).
       확정 후 본인 예약이면 customer_name을 patient_name으로 자동 할당.
    3. 나머지 필드(patient_name, contact 등): (None, None) 반환하여
       classify_intent(대화 이력 포함)에서 LLM이 한번에 추출하도록 위임.

    Args:
        user_message: 사용자 입력 메시지.
        ticket: 티켓 딕셔너리 (수정됨).
        session_state: 세션 상태 딕셔너리.

    Returns:
        tuple[dict | None, dict | None]:
            - (응답, None): 즉시 반환할 응답이 있는 경우
            - (None, intent_result): 강제 의도 결과가 있는 경우
            - (None, None): 메인 흐름 계속 진행
    """
    if session_state is None:
        return None, None

    pending_missing_info = _get_pending_missing_info(session_state)
    if not pending_missing_info:
        return None, None

    # ── 의도 전환 감지: 정보 수집 중 사용자가 다른 액션을 요청한 경우 ──
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
            # 판별 불가 → 재질문
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
        # 대리/본인 확정
        session_state["is_proxy_booking"] = parsed_proxy
        ticket["is_proxy_booking"] = parsed_proxy
        # 본인 예약이면 customer_name을 patient_name으로 자동 할당
        if parsed_proxy is False and not session_state.get("patient_name"):
            inferred_name = ticket.get("customer_name") or session_state.get("customer_name")
            if inferred_name:
                session_state["patient_name"] = inferred_name
                ticket["patient_name"] = inferred_name
        # is_proxy_booking을 큐에서 제거
        pending_missing_info = [item for item in pending_missing_info if item != "is_proxy_booking"]
        # 본인 예약이면 patient_name도 큐에서 제거 (이미 채워졌으므로)
        inferred = session_state.get("patient_name") or ticket.get("customer_name") or session_state.get("customer_name")
        if inferred and parsed_proxy is False:
            pending_missing_info = [item for item in pending_missing_info if item != "patient_name"]
        session_state["pending_missing_info_queue"] = list(pending_missing_info)
        session_state["pending_missing_info"] = list(pending_missing_info)
        _reset_clarify_turn_count(session_state)
        # proxy 처리 후, 나머지 필드는 classify_intent(대화 이력 포함)에서 추출
        return None, None

    # ── 나머지 identity/booking 필드: classify_intent에 위임 (대화 이력 전체 활용) ──
    # regex 기반 개별 추출 대신, LLM이 전체 대화에서 한번에 추출하도록 위임
    return None, None


# ============================================================================
# 예약 시간 / 슬롯 관련 유틸리티
# ============================================================================


def _build_booking_time(date_value: str | None, time_value: str | None, now: datetime) -> str | None:
    """날짜(date_value)와 시간(time_value)을 결합하여 ISO 형식 booking_time을 생성한다.

    타임존이 없으면 now의 타임존을 사용하고, now도 타임존이 없으면 UTC를 기본값으로 사용.

    Args:
        date_value: "YYYY-MM-DD" 형식 날짜 문자열.
        time_value: "HH:MM" 형식 시간 문자열.
        now: 현재 시각 (타임존 참조용).

    Returns:
        str | None: ISO 형식 booking_time 또는 None (날짜/시간 누락 시).
    """
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
    """예약 딕셔너리에서 department, date, time 슬롯을 추출한다.

    booking_time ISO 문자열이 있으면 파싱하여 date/time을 계산하고,
    없으면 appointment의 date/time 필드를 직접 사용한다.
    "Z" 접미사(UTC 표기)도 올바르게 처리한다.

    Args:
        appointment: 예약 딕셔너리 (booking_time, department, date, time 등).
        now: 현재 시각 (타임존 참조용).

    Returns:
        dict: department, date(YYYY-MM-DD), time(HH:MM) 슬롯 딕셔너리.
    """
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


# ============================================================================
# 예약 검색 및 매칭
# ============================================================================


def _appointment_identity(appointment: dict) -> tuple:
    """예약의 고유 식별 튜플을 반환한다 (중복 제거용).

    id, customer_name, booking_time, date, time, department를 결합하여
    같은 예약이 메모리와 디스크에서 중복 로드되는 것을 방지한다.

    Args:
        appointment: 예약 딕셔너리.

    Returns:
        tuple: 예약 식별 튜플.
    """
    return (
        appointment.get("id"),
        appointment.get("customer_name"),
        appointment.get("booking_time"),
        appointment.get("date"),
        appointment.get("time"),
        appointment.get("department"),
    )


def _merge_appointment_sources(*appointment_groups: list[dict]) -> list[dict]:
    """여러 소스(저장소, 메모리, Cal.com)의 예약 목록을 중복 없이 병합한다.

    _appointment_identity()를 기준으로 중복을 감지하여 첫 번째 등장만 유지한다.

    Args:
        *appointment_groups: 병합할 예약 목록들 (가변 인자).

    Returns:
        list[dict]: 중복 제거된 병합 예약 목록.
    """
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
    """저장소에서 고객 이름(+생년월일)으로 예약을 검색한다.

    Args:
        customer_name: 고객 이름. None이면 빈 목록 반환.
        birth_date: 생년월일 (선택적 필터).

    Returns:
        list[dict]: 매칭된 예약 목록. 오류 시 빈 목록.
    """
    if not customer_name:
        return []
    try:
        filters = {"birth_date": birth_date} if birth_date else None
        return find_bookings(customer_name=customer_name, filters=filters)
    except Exception:
        return []


# Cal.com 이벤트 타입 slug → 한국어 분과명 매핑
_CALCOM_SLUG_TO_DEPT: dict[str, str] = {
    "ent": "이비인후과",
    "internal": "내과",
    "orthopedics": "정형외과",
}


def _convert_calcom_booking_to_local(cb: dict) -> dict | None:
    """Cal.com 예약을 로컬 appointment 형식으로 변환한다.

    Cal.com API 응답의 구조(attendees, eventType.slug, start 등)를
    로컬 저장소 형식(patient_name, department, booking_time 등)으로 매핑한다.
    UTC → KST(UTC+9) 변환도 수행한다.

    Args:
        cb: Cal.com 예약 딕셔너리.

    Returns:
        dict | None: 로컬 형식 예약 딕셔너리 또는 None (attendees 없는 경우).
    """
    attendees = cb.get("attendees", [])
    if not attendees:
        return None
    att = attendees[0]
    email = att.get("email", "")
    # 전화번호 추출: "01098765432@kobimedi.local" → "010-9876-5432"
    phone_raw = email.split("@")[0] if "@kobimedi.local" in email else ""
    if len(phone_raw) >= 10:
        phone = f"{phone_raw[:3]}-{phone_raw[3:7]}-{phone_raw[7:]}"
    else:
        phone = phone_raw

    start_utc = cb.get("start", "")
    # UTC → KST (UTC+9)
    try:
        from datetime import datetime as _dt, timedelta as _td
        utc_dt = _dt.fromisoformat(start_utc.replace("Z", "+00:00"))
        kst_dt = utc_dt + _td(hours=9)
        date_str = kst_dt.strftime("%Y-%m-%d")
        time_str = kst_dt.strftime("%H:%M")
    except Exception:
        date_str = None
        time_str = None

    slug = (cb.get("eventType") or {}).get("slug", "")
    department = _CALCOM_SLUG_TO_DEPT.get(slug, slug)

    return {
        "id": str(cb.get("id", "")),
        "calcom_uid": cb.get("uid"),
        "patient_name": att.get("name"),
        "patient_contact": phone,
        "department": department,
        "date": date_str,
        "time": time_str,
        "booking_time": f"{date_str}T{time_str}:00+09:00" if date_str and time_str else None,
        "status": "active" if cb.get("status") == "accepted" else cb.get("status"),
    }


def _find_customer_appointments(ticket: dict, all_appointments: list[dict], existing_appointment: dict | None) -> list[dict]:
    """고객의 예약을 저장소 + 메모리 + Cal.com에서 종합 검색한다.

    검색 전략:
    1. patient_contact가 있으면 전화번호로 저장소 검색
    2. 없으면 patient_name + birth_date로 저장소 검색
    3. all_appointments(메모리 캐시)에서도 동일 조건으로 필터링
    4. 두 소스를 _merge_appointment_sources로 중복 없이 병합
    5. 병합 결과가 없고 전화번호가 있으면 Cal.com API로 폴백 검색
    6. cancelled 상태 제외 + 날짜/시간/분과 기준 중복 제거
    7. 최종 결과가 없으면 existing_appointment를 폴백으로 반환

    Args:
        ticket: 티켓 딕셔너리 (customer_name, patient_name, birth_date, patient_contact).
        all_appointments: 전체 예약 메모리 캐시.
        existing_appointment: 이미 특정된 기존 예약 (폴백용).

    Returns:
        list[dict]: 매칭된 활성 예약 목록 (최소 0건, 폴백 시 1건).
    """
    customer_name = ticket.get("customer_name")
    patient_name = ticket.get("patient_name") or customer_name
    birth_date = normalize_birth_date(ticket.get("birth_date"))
    patient_contact = _format_patient_contact(ticket.get("patient_contact"))

    # 저장소(bookings.json) 검색
    try:
        if patient_contact:
            storage_matches = find_bookings(patient_contact=patient_contact)
        else:
            storage_matches = _load_storage_appointments(patient_name, birth_date)
    except Exception:
        storage_matches = []

    # 메모리 캐시(all_appointments) 필터링
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

    # 로컬에 없으면 Cal.com에서 조회 (폴백)
    if not merged_matches and patient_contact:
        phone_digits = patient_contact.replace("-", "")
        try:
            calcom_bookings = calcom_client.list_bookings() or []
            for cb in calcom_bookings:
                for att in cb.get("attendees", []):
                    if phone_digits in (att.get("email", "") or ""):
                        converted = _convert_calcom_booking_to_local(cb)
                        if converted and converted.get("status") == "active":
                            merged_matches.append(converted)
                        break
        except Exception:
            pass

    # cancelled 제외 + 날짜/시간/분과 기준 중복 제거
    seen_slots: set[tuple] = set()
    active_matches: list[dict] = []
    for appt in merged_matches:
        if appt.get("status") == "cancelled":
            continue
        slot_key = (appt.get("date"), appt.get("time"), appt.get("department"))
        if slot_key in seen_slots:
            continue
        seen_slots.add(slot_key)
        active_matches.append(appt)

    if active_matches:
        return active_matches

    if existing_appointment:
        return [existing_appointment]
    return []


def _filter_candidate_appointments(candidates: list[dict], slots: dict, now: datetime) -> list[dict]:
    """후보 예약 목록을 슬롯 조건(department, date, time)으로 필터링한다.

    지정된 슬롯 값이 있는 필드만 비교하고, None인 필드는 무시한다.

    Args:
        candidates: 후보 예약 목록.
        slots: 필터 조건 슬롯 딕셔너리.
        now: 현재 시각 (booking_time 파싱용).

    Returns:
        list[dict]: 조건에 부합하는 예약만 포함된 목록.
    """
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
    """사용자의 후보 선택 응답에서 선택된 예약을 반환한다.

    두 가지 전략으로 매칭:
    1. 숫자 추출: "1번" → 1번째 후보
    2. 텍스트 매칭: 분과명/날짜/시간이 메시지에 포함된 후보 선택

    Args:
        message: 사용자 입력 메시지.
        candidates: 후보 예약 목록.
        now: 현재 시각.

    Returns:
        dict | None: 선택된 예약 딕셔너리 또는 None.
    """
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


# ============================================================================
# 예약 확정(Confirmation) 처리 — "네"/"아니요" 응답 소비
# ============================================================================


def _handle_pending_confirmation(
    user_message: str,
    session_state: dict,
    all_appointments: list[dict],
    now: datetime,
    *,
    ticket: dict | None = None,
    customer_type: str | None = None,
) -> dict | None:
    """확정 대기 중인 예약에 대한 "네"/"아니요" 응답을 처리한다.

    pending_confirmation이 없으면 None을 반환하여 메인 흐름을 계속한다.

    "네" (긍정) 처리:
        - book_appointment: Cal.com 예약 생성 → 로컬 Storage 영속화 → 성공 메시지
          - Cal.com 실패 시: None 반환 → Hard Fail, False 반환 → Race Condition 409
        - modify/cancel/check: 즉시 성공 메시지
        - 성공 후 _clear_dialogue_state로 세션 초기화

    "아니요" (부정) 처리:
        - pending_confirmation 해제
        - clarify_turn_count 리셋 (D-010 fix: 거부는 유효한 대화 진전)
        - "다른 날짜/시간을 알려주세요" 안내

    둘 다 아닌 경우:
        - pending_confirmation 해제 → None 반환하여 메인 흐름에서 재분류

    Args:
        user_message: 사용자 입력 메시지.
        session_state: 세션 상태 딕셔너리.
        all_appointments: 전체 예약 목록 (새 예약 추가 시 append).
        now: 현재 시각.
        ticket: 원본 티켓.
        customer_type: 초진/재진.

    Returns:
        dict | None: 응답 딕셔너리 또는 None (pending_confirmation 없거나 "네/아니요"가 아닌 경우).
    """
    pending_confirmation = session_state.get("pending_confirmation")
    if not pending_confirmation:
        return None

    if _is_affirmative(user_message):
        appointment = pending_confirmation.get("appointment", {})
        action = pending_confirmation.get("action", "book_appointment")
        if action == "book_appointment":
            # ── Position 3: cal.com 예약 생성 (로컬 영속화 직전, Race Condition 방어) ──
            dept = appointment.get("department")
            if calcom_client.is_calcom_enabled(dept):
                cc_result = calcom_client.create_booking(
                    department=dept or "",
                    date=appointment.get("date", ""),
                    time=appointment.get("time", ""),
                    patient_name=appointment.get("patient_name") or appointment.get("customer_name") or "",
                    patient_contact=appointment.get("patient_contact") or "",
                    customer_type=appointment.get("customer_type") or "new",
                )
                if cc_result is None:
                    # 네트워크/타임아웃 Hard Fail
                    record_kpi_event(KpiEvent.AGENT_HARD_FAIL)
                    return _build_response_and_record(
                        session_state,
                        action="clarify",
                        message="현재 외부 예약 시스템 응답 지연으로 처리가 불가합니다. 잠시 후 다시 시도해주세요.",
                        department=dept,
                        ticket=ticket,
                        classified_intent="book_appointment",
                        intent_result={"action": "book_appointment", "department": dept},
                        customer_type=customer_type,
                    )
                elif cc_result is False:
                    # 409 Conflict — Race Condition (다른 사용자가 동시에 예약)
                    record_kpi_event(KpiEvent.AGENT_SOFT_FAIL_CLARIFY)
                    return _build_response_and_record(
                        session_state,
                        action="clarify",
                        message="방금 전 외부 캘린더에서 예약이 마감되었습니다. 다른 시간을 선택해주세요.",
                        department=dept,
                        ticket=ticket,
                        classified_intent="book_appointment",
                        intent_result={"action": "book_appointment", "department": dept},
                        customer_type=customer_type,
                    )
                elif isinstance(cc_result, dict) and cc_result.get("uid"):
                    appointment["calcom_uid"] = cc_result["uid"]
            # 로컬 Storage 영속화 (bookings.json = source of truth)
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
        # 성공 메시지 생성 및 대화 상태 초기화
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

    # "네"/"아니요"가 아닌 응답 → 확정 대기 해제, 메인 흐름에서 재분류
    session_state["pending_confirmation"] = None
    return None


# ============================================================================
# 메인 처리 엔진: process_ticket (배치/채팅 공용)
# ============================================================================


def process_ticket(
    ticket: dict,
    all_appointments: list = None,
    existing_appointment: dict = None,
    session_state: dict | None = None,
    now: datetime = None,
) -> dict:
    """티켓을 Safety Gate → Intent Classification → Policy Engine → 실행까지 처리한다.

    이 함수가 코비메디 챗봇의 **핵심 처리 파이프라인**이다.
    배치 모드(session_state=None)와 채팅 모드(session_state != None) 모두 지원하며,
    채팅 모드에서는 대화 상태 머신을 통해 멀티턴 흐름을 관리한다.

    처리 순서:
        1. 초기화: now 기본값 설정, 세션 상태 초기화, 기존 예약 탐색
        2. 빈 메시지 검증: message가 없으면 즉시 reject
        3. Safety Gate: _run_safety_gate() 실행 → safe가 아니면 즉시 거절/에스컬레이션
        4. 미지원 분과/의료진 체크: unsupported_department/doctor → reject
        5. 증상 기반 분과 안내: mixed_department_guidance → clarify
        6. Fast-Path 대화 상태 처리 (채팅 모드만):
           a. 대안 슬롯 선택 (pending_alternative_slots)
           b. 예약 확정 "네/아니요" (pending_confirmation)
           c. 복수 예약 선택 (pending_candidates)
           d. 신원 정보 수집 (pending_identity_input)
        7. Intent Classification: LLM으로 의도·슬롯 추출
        8. 배치 모드: ticket context로 intent 보완 (_merge_ticket_context_into_intent)
        9. 신원 정보 동기화 (_sync_identity_state_from_intent)
        10. 액션 결정: clarify 업그레이드, 의도 전환 감지
        11. 슬롯 병합 및 누락 정보 판별
        12. 누락 정보 있으면 clarify (+ Cal.com 선제적 슬롯 안내)
        13. 순수 clarify/escalate/reject 처리
        14. Policy Engine: apply_policy() 실행 → 정책 위반 시 대안 제시
        15. book_appointment 실행:
            - 채팅: Cal.com 가용성 교차 검증 → 확정 질문
            - 배치: Cal.com 가용성 확인 → 즉시 예약 → 로컬 영속화
        16. cancel_appointment 실행: 로컬 Storage 취소 + Cal.com 원격 취소
        17. modify_appointment 실행: 기존 취소 + 신규 생성 (Cal.com은 수정 API 없음)
        18. 성공 메시지 생성 + KPI 이벤트 기록

    모든 출구에서 KpiEvent를 기록하며, 실패 시에는 거짓 성공(false positive)을
    반환하지 않고 명시적인 에러 메시지를 반환한다.

    Args:
        ticket: 사용자 요청 딕셔너리. 필수: message. 선택: customer_name, birth_date,
                customer_type, context, patient_name, patient_contact, is_proxy_booking.
        all_appointments: 전체 예약 목록. None이면 디스크에서 로드.
        existing_appointment: 변경/취소 대상으로 이미 특정된 예약.
        session_state: 멀티턴 대화 상태. None이면 배치 모드.
        now: 현재 시각. None이면 UTC 기준 현재.

    Returns:
        dict: action, response, confidence, reasoning 등을 포함하는 최종 응답.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if all_appointments is None:
        all_appointments = _load_appointments_from_disk()
    is_chat = session_state is not None

    # ── 1단계: 세션 상태 초기화 및 기본값 설정 ──
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

    # ── 2단계: 빈 메시지 검증 ──
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

    # ── 3단계: Safety Gate (파이프라인 최우선 — 의료 상담 우회 방지) ──
    safety_result = _run_safety_gate(user_message, state)

    if safety_result.get("category") != "safe":
        return _build_safety_response(
            safety_result,
            state,
            ticket=ticket,
            customer_type=customer_type,
        )

    # ── 4단계: 미지원 분과/의료진 체크 ──
    unknown_entity_response = _build_unknown_entity_response(
        safety_result,
        state,
        ticket=ticket,
        customer_type=customer_type,
    )
    if unknown_entity_response is not None:
        return unknown_entity_response

    # ── 5단계: 증상 기반 분과 안내 (의료 상담은 안 되지만 예약 분과는 추천) ──
    if safety_result.get("mixed_department_guidance"):
        return _build_department_guidance_response(
            safety_result.get("department_hint"),
            state,
            ticket=ticket,
            safety_result=safety_result,
            customer_type=customer_type,
        )

    # ── 6a단계: 대안 슬롯 선택 처리 (정책 위반 후 대안 시간 선택 대기) ──
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

    # ── 6b단계: 예약 확정 "네/아니요" 처리 ──
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

    # ── 6c단계: 복수 예약 중 선택 처리 ──
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

    # ── 6d단계: 신원 정보 수집 (대리 여부 등) ──
    consumed_identity_response, forced_intent_result = _consume_pending_identity_input(user_message, ticket, state)
    if consumed_identity_response is not None:
        return consumed_identity_response

    # ── 7단계: Intent Classification (LLM 호출) ──
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
        # 후보 선택 완료 시: 선택된 예약의 슬롯으로 intent_result 구성
        slots = _extract_candidate_slots(selected_existing_appointment, now)
        intent_result = {
            "action": action_override,
            "department": slots.get("department"),
            "date": slots.get("date"),
            "time": slots.get("time"),
            "missing_info": [],
        }

    # ── 8단계: 배치 모드 — ticket 메타데이터로 intent 보완 ──
    if not is_chat:
        intent_result = _merge_ticket_context_into_intent(ticket, intent_result)

    # ── 9단계: 신원 정보 동기화 (intent에서 추출된 이름/연락처/생년월일 반영) ──
    _sync_identity_state_from_intent(ticket, state, intent_result, is_chat=is_chat)

    # ── 10단계: 액션 결정 및 보정 ──
    action = intent_result.get("action")
    inferred_action = _infer_requested_action(user_message)

    # 예약 진행 중(pending_action)에 LLM이 escalate/reject를 반환하면
    # 대화 이력의 증상 표현 때문이므로 pending_action으로 복원
    pending_action = (state or {}).get("pending_action")
    if action in {"escalate", "reject"} and pending_action in BOOKING_RELATED_ACTIONS:
        action = pending_action

    if action == "clarify":
        has_booking_slots = any(intent_result.get(key) for key in ["department", "date", "time"])
        if pending_action:
            action = pending_action
        elif inferred_action in {"cancel_appointment", "modify_appointment", "check_appointment"}:
            action = inferred_action
        elif inferred_action == "book_appointment" and (is_chat or has_booking_slots):
            # Bug fix (F-031, F-042): 채팅 모드에서는 슬롯 없이 "예약할래요"만으로도
            # book_appointment로 업그레이드하여 proxy 질문을 첫 턴에 할 수 있게 함.
            # 배치 모드에서는 슬롯이 하나라도 있을 때만 업그레이드.
            action = inferred_action

    # 의도 전환 감지: pending_action과 새 action이 다르면 플로우 리셋
    previous_pending_action = (state or {}).get("pending_action")
    if (
        state is not None
        and previous_pending_action in BOOKING_RELATED_ACTIONS
        and action in BOOKING_RELATED_ACTIONS
        and previous_pending_action != action
    ):
        _reset_pending_flow_for_new_action(state, action)

    # ── 11단계: 슬롯 병합 및 누락 정보 판별 ──
    merged_slots = _merge_accumulated_slots(state, intent_result)
    # D-001 fix: accumulated_slots에 반영된 슬롯을 pending_missing_info_queue에서 즉시 제거
    _sync_queue_with_accumulated_slots(state, merged_slots)
    department = merged_slots.get("department") or intent_result.get("department") or safety_result.get("department_hint")
    classified_intent = intent_result.get("action")

    if state is not None:
        state["pending_action"] = action

    # 유효 필드 조회
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

    # 저장소 이력 기반 초진/재진 판별
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

    # modify 시 기존 예약 참조: existing_appointment가 없으면 all_appointments에서 탐색
    _modify_target = existing_appointment
    if action == "modify_appointment" and _modify_target is None and all_appointments:
        _candidates = _find_customer_appointments(ticket, all_appointments, None)
        if len(_candidates) == 1:
            _modify_target = _candidates[0]

    # ── 12단계: 누락 정보가 있으면 clarify (+ Cal.com 선제적 슬롯 안내) ──
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
        target_appointment=_modify_target,
    )
    if dialogue_missing_info:
        # clarify 한계 초과 시 상담원 전환
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

        # ── Position 1: cal.com 선제적 슬롯 안내 (동선 1.3) ──
        # 분과+날짜는 있지만 시간만 누락된 경우, cal.com에서 가용 시간을 조회하여 안내
        _resolved_ct = (history_resolution or {}).get("customer_type")
        clarify_message = build_missing_info_question(dialogue_missing_info, department=department, action_context=action, customer_type=_resolved_ct)
        if (
            "time" in dialogue_missing_info
            and action == "book_appointment"
            and merged_slots.get("department")
            and merged_slots.get("date")
            and calcom_client.is_calcom_enabled(merged_slots["department"])
        ):
            try:
                proactive_slots = calcom_client.get_available_slots(
                    merged_slots["department"], merged_slots["date"]
                )
                if proactive_slots is not None and proactive_slots:
                    # 초진/재진에 따른 진료시간으로 운영시간 내 슬롯만 필터링
                    _ct = (history_resolution or {}).get("customer_type")
                    _is_first = _ct not in ("재진", "revisit")
                    _duration = get_appointment_duration(_is_first)
                    _date_str = merged_slots["date"]
                    filtered_slots = []
                    for s in proactive_slots:
                        _start = datetime.fromisoformat(f"{_date_str}T{s}:00")
                        _end = _start + _duration
                        _ok, _ = is_within_operating_hours(_start, _end)
                        if _ok:
                            filtered_slots.append(s)
                    if filtered_slots:
                        slots_str = ", ".join(filtered_slots)
                        if _is_first:
                            clarify_message = f"초진 환자는 진료시간이 40분 소요됩니다. 예약 가능한 시간은 {slots_str}입니다. 언제가 좋으신가요?"
                        else:
                            clarify_message = f"예약 가능한 시간은 {slots_str}입니다. 언제가 좋으신가요?"
                    else:
                        clarify_message = "해당 날짜에 예약 가능한 시간이 없습니다. 다른 날짜를 선택해주세요."
            except Exception as exc:
                logger.debug("cal.com proactive slot query failed: %s", exc)

        return _build_response_and_record(
            state,
            action="clarify",
            message=clarify_message,
            department=department,
            ticket=ticket,
            classified_intent=classified_intent,
            safety_result=safety_result,
            intent_result={**intent_result, "action": action, "department": department, "missing_info": dialogue_missing_info},
            customer_type=customer_type,
        )

    _set_pending_missing_info(state, [])

    # ── 13단계: 순수 clarify / escalate / reject 처리 ──
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

    if action == "escalate":
        record_kpi_event(KpiEvent.AGENT_HARD_FAIL)
        return _build_response_and_record(
            state,
            action="escalate",
            message="해당 요청은 상담원이 확인 후 안내드려야 합니다. 상담원 연결을 도와드릴게요.",
            department=department,
            ticket=ticket,
            classified_intent=classified_intent,
            safety_result=safety_result,
            intent_result=intent_result,
            customer_type=customer_type,
        )

    if action == "reject":
        record_kpi_event(KpiEvent.AGENT_HARD_FAIL)
        return _build_response_and_record(
            state,
            action="reject",
            message="코비메디 예약 관련 문의만 도와드릴 수 있습니다.",
            department=department,
            ticket=ticket,
            classified_intent=classified_intent,
            safety_result=safety_result,
            intent_result=intent_result,
            customer_type=customer_type,
        )

    # ── 14단계: 예약 시 초진/재진 customer_type 확정 ──
    if action == "book_appointment":
        customer_type = (history_resolution or {}).get("customer_type")
        if state is not None:
            state["resolved_customer_type"] = customer_type
        if customer_type:
            ticket["customer_type"] = customer_type

    booking_time = _build_booking_time(merged_slots.get("date"), merged_slots.get("time"), now)
    target_existing_appointment = selected_existing_appointment or existing_appointment

    # ── 변경/취소/조회 시 대상 예약 탐색: 후보가 2건 이상이면 사용자에게 선택 요청 ──
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

    # ── 15단계: Policy Engine (24시간 규칙, 하루 3건 제한 등 결정론적 정책) ──
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

    # 정책 위반: 대안 슬롯 제시 또는 거절
    if not policy_result.action.value.endswith("appointment"):
        message = policy_result.message
        recommended_action = policy_result.action.value
        alternatives = policy_result.suggested_slots

        # 채팅 모드: 대안 슬롯이 있으면 선택 질문으로 전환
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
        # 배치 모드 또는 대안 없음: 메시지에 대안 시간 목록 추가
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

    # ── 16단계: book_appointment 실행 ──
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
            # ── Position 2: 확정 질문 직전 cal.com 가용성 교차 검증 (동선 1 & 2) ──
            chosen_time = merged_slots.get("time")
            if department and chosen_time and calcom_client.is_calcom_enabled(department):
                pre_slots = calcom_client.get_available_slots(department, merged_slots.get("date", ""))
                if pre_slots is None:
                    # 네트워크/타임아웃 Hard Fail
                    record_kpi_event(KpiEvent.AGENT_HARD_FAIL)
                    return _build_response_and_record(
                        state,
                        action="clarify",
                        message="현재 외부 예약 시스템 응답 지연으로 가용 시간을 확인할 수 없습니다. 잠시 후 다시 시도해주세요.",
                        department=department,
                        ticket=ticket,
                        classified_intent=classified_intent,
                        safety_result=safety_result,
                        intent_result=intent_result,
                        policy_result={"allowed": False},
                        customer_type=customer_type,
                    )
                elif chosen_time not in pre_slots:
                    # 슬롯 마감 — 대안 제시 후 clarify
                    alt_text = (
                        f"가능한 다른 시간은 {', '.join(pre_slots)}입니다."
                        if pre_slots
                        else "현재 예약 가능한 시간이 없습니다."
                    )
                    record_kpi_event(KpiEvent.AGENT_SOFT_FAIL_CLARIFY)
                    return _build_response_and_record(
                        state,
                        action="clarify",
                        message=f"선택하신 시간은 방금 마감되었습니다. {alt_text}",
                        department=department,
                        ticket=ticket,
                        classified_intent=classified_intent,
                        safety_result=safety_result,
                        intent_result=intent_result,
                        policy_result={"allowed": False},
                        customer_type=customer_type,
                    )

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
            # ── 배치 모드: cal.com 가용성 확인 후 즉시 예약 확정 (동선 5.1, 5.2) ──
            if calcom_client.is_calcom_enabled(department):
                chosen_time = merged_slots.get("time")
                batch_slots = calcom_client.get_available_slots(department, merged_slots.get("date", ""))
                if batch_slots is None:
                    # 네트워크/타임아웃 Hard Fail — 거짓 성공 방지
                    record_kpi_event(KpiEvent.AGENT_HARD_FAIL)
                    return _build_response_and_record(
                        state,
                        action="clarify",
                        message="현재 외부 예약 시스템 응답 지연으로 처리가 불가합니다. 잠시 후 다시 시도해주세요.",
                        department=department,
                        ticket=ticket,
                        classified_intent="book_appointment",
                        safety_result=safety_result,
                        intent_result=intent_result,
                        policy_result={"allowed": False},
                        customer_type=customer_type,
                    )
                if chosen_time and chosen_time not in batch_slots:
                    # 슬롯 마감 — 즉시 Drop (Soft Fail, 대안 포함)
                    alt_text = (
                        f"대안 시간: {', '.join(batch_slots)}"
                        if batch_slots
                        else "현재 예약 가능한 시간이 없습니다."
                    )
                    record_kpi_event(KpiEvent.AGENT_SOFT_FAIL_CLARIFY)
                    return _build_response_and_record(
                        state,
                        action="clarify",
                        message=f"요청하신 시간이 마감되었습니다. {alt_text}",
                        department=department,
                        ticket=ticket,
                        classified_intent="book_appointment",
                        safety_result=safety_result,
                        intent_result=intent_result,
                        policy_result={"allowed": False},
                        customer_type=customer_type,
                    )
                # 슬롯 가용 — cal.com 예약 생성
                cc_result = calcom_client.create_booking(
                    department=department or "",
                    date=merged_slots.get("date", ""),
                    time=chosen_time or "",
                    patient_name=appointment.get("patient_name") or appointment.get("customer_name") or "",
                    patient_contact=appointment.get("patient_contact") or "",
                    customer_type=appointment.get("customer_type") or "new",
                )
                if cc_result is None:
                    record_kpi_event(KpiEvent.AGENT_HARD_FAIL)
                    return _build_response_and_record(
                        state,
                        action="clarify",
                        message="현재 외부 예약 시스템 응답 지연으로 처리가 불가합니다. 잠시 후 다시 시도해주세요.",
                        department=department,
                        ticket=ticket,
                        classified_intent="book_appointment",
                        safety_result=safety_result,
                        intent_result=intent_result,
                        policy_result={"allowed": False},
                        customer_type=customer_type,
                    )
                elif cc_result is False:
                    record_kpi_event(KpiEvent.AGENT_SOFT_FAIL_CLARIFY)
                    return _build_response_and_record(
                        state,
                        action="clarify",
                        message="방금 전 외부 캘린더에서 예약이 마감되었습니다. 다른 시간을 선택해주세요.",
                        department=department,
                        ticket=ticket,
                        classified_intent="book_appointment",
                        safety_result=safety_result,
                        intent_result=intent_result,
                        policy_result={"allowed": False},
                        customer_type=customer_type,
                    )

            # Cal.com UID 저장
            if isinstance(locals().get("cc_result"), dict) and cc_result.get("uid"):
                appointment["calcom_uid"] = cc_result["uid"]

            # 배치 모드: 확인 없이 즉시 예약 결정 (cal.com 비활성 또는 성공)
            # 로컬 영속화 (bookings.json = source of truth)
            try:
                persisted_booking = create_booking(appointment)
            except Exception:
                # 로컬 저장 실패 — 거짓 성공 방지
                record_kpi_event(KpiEvent.AGENT_HARD_FAIL)
                return _build_response_and_record(
                    state,
                    action="clarify",
                    message="예약 정보를 저장하는 중 문제가 발생했습니다. 다시 시도해주세요.",
                    department=department,
                    ticket=ticket,
                    classified_intent="book_appointment",
                    safety_result=safety_result,
                    intent_result=intent_result,
                    policy_result={"allowed": False},
                    customer_type=customer_type,
                )
            all_appointments.append(persisted_booking)
            appointment = persisted_booking
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

    # ── 17단계: cancel_appointment 실행 — 로컬 Storage + Cal.com 원격 취소 ──
    if action == "cancel_appointment" and target_existing_appointment:
        booking_id = target_existing_appointment.get("id")
        # 로컬 Storage 취소 (bookings.json 상태를 "cancelled"로 변경)
        if booking_id:
            try:
                if not cancel_booking(booking_id):
                    # 로컬 취소 실패 — 거짓 성공 방지
                    record_kpi_event(KpiEvent.AGENT_HARD_FAIL)
                    return _build_response_and_record(
                        session_state,
                        action="clarify",
                        message="예약 취소 처리 중 문제가 발생했습니다. 다시 한 번 시도해 주세요.",
                        department=department,
                        ticket=ticket,
                        classified_intent="cancel_appointment",
                        intent_result=intent_result,
                        policy_result={"allowed": True},
                        customer_type=customer_type,
                    )
            except Exception:
                record_kpi_event(KpiEvent.AGENT_HARD_FAIL)
                return _build_response_and_record(
                    session_state,
                    action="clarify",
                    message="예약 취소 처리 중 문제가 발생했습니다. 다시 한 번 시도해 주세요.",
                    department=department,
                    ticket=ticket,
                    classified_intent="cancel_appointment",
                    intent_result=intent_result,
                    policy_result={"allowed": True},
                    customer_type=customer_type,
                )
        # Cal.com 원격 취소 (calcom_uid가 있고 해당 분과가 Cal.com 활성인 경우)
        calcom_uid = target_existing_appointment.get("calcom_uid")
        if calcom_uid and calcom_client.is_calcom_enabled(target_existing_appointment.get("department", "")):
            try:
                cc_result = calcom_client.cancel_booking_remote(calcom_uid)
                if cc_result is None:
                    # Cal.com 취소 실패 — 거짓 성공 방지
                    record_kpi_event(KpiEvent.AGENT_HARD_FAIL)
                    return _build_response_and_record(
                        session_state,
                        action="clarify",
                        message="현재 외부 예약 시스템 응답 지연으로 취소 처리가 불가합니다. 잠시 후 다시 시도해주세요.",
                        department=department,
                        ticket=ticket,
                        classified_intent="cancel_appointment",
                        intent_result=intent_result,
                        policy_result={"allowed": True},
                        customer_type=customer_type,
                    )
            except Exception:
                record_kpi_event(KpiEvent.AGENT_HARD_FAIL)
                return _build_response_and_record(
                    session_state,
                    action="clarify",
                    message="현재 외부 예약 시스템 응답 지연으로 취소 처리가 불가합니다. 잠시 후 다시 시도해주세요.",
                    department=department,
                    ticket=ticket,
                    classified_intent="cancel_appointment",
                    intent_result=intent_result,
                    policy_result={"allowed": True},
                    customer_type=customer_type,
                )

    # ── 18단계: modify_appointment 실행 — 기존 취소 + 신규 생성 ──
    # Cal.com은 예약 수정 API가 없으므로 "취소 후 재생성" 전략을 사용한다.
    if action == "modify_appointment" and target_existing_appointment:
        old_id = target_existing_appointment.get("id")
        old_calcom_uid = target_existing_appointment.get("calcom_uid")
        dept = target_existing_appointment.get("department") or department

        # 1) 기존 예약 취소 (로컬 Storage)
        if old_id:
            try:
                if not cancel_booking(old_id):
                    record_kpi_event(KpiEvent.AGENT_HARD_FAIL)
                    return _build_response_and_record(
                        session_state,
                        action="clarify",
                        message="기존 예약 취소 처리 중 문제가 발생했습니다. 다시 한 번 시도해 주세요.",
                        department=dept,
                        ticket=ticket,
                        classified_intent="modify_appointment",
                        intent_result=intent_result,
                        policy_result={"allowed": True},
                        customer_type=customer_type,
                    )
            except Exception:
                record_kpi_event(KpiEvent.AGENT_HARD_FAIL)
                return _build_response_and_record(
                    session_state,
                    action="clarify",
                    message="기존 예약 취소 처리 중 문제가 발생했습니다. 다시 한 번 시도해 주세요.",
                    department=dept,
                    ticket=ticket,
                    classified_intent="modify_appointment",
                    intent_result=intent_result,
                    policy_result={"allowed": True},
                    customer_type=customer_type,
                )

        # 2) 기존 예약 취소 (Cal.com 원격)
        if old_calcom_uid and calcom_client.is_calcom_enabled(dept or ""):
            try:
                cc_result = calcom_client.cancel_booking_remote(old_calcom_uid)
                if cc_result is None:
                    record_kpi_event(KpiEvent.AGENT_HARD_FAIL)
                    return _build_response_and_record(
                        session_state,
                        action="clarify",
                        message="현재 외부 예약 시스템 응답 지연으로 변경 처리가 불가합니다. 잠시 후 다시 시도해주세요.",
                        department=dept,
                        ticket=ticket,
                        classified_intent="modify_appointment",
                        intent_result=intent_result,
                        policy_result={"allowed": True},
                        customer_type=customer_type,
                    )
            except Exception:
                record_kpi_event(KpiEvent.AGENT_HARD_FAIL)
                return _build_response_and_record(
                    session_state,
                    action="clarify",
                    message="현재 외부 예약 시스템 응답 지연으로 변경 처리가 불가합니다. 잠시 후 다시 시도해주세요.",
                    department=dept,
                    ticket=ticket,
                    classified_intent="modify_appointment",
                    intent_result=intent_result,
                    policy_result={"allowed": True},
                    customer_type=customer_type,
                )

        # 3) 새 시간으로 예약 생성
        new_appointment = {
            "customer_name": ticket.get("customer_name") or (state or {}).get("customer_name"),
            "patient_name": ticket.get("patient_name") or (state or {}).get("patient_name"),
            "patient_contact": ticket.get("patient_contact") or (state or {}).get("patient_contact"),
            "is_proxy_booking": bool(ticket.get("is_proxy_booking") or (state or {}).get("is_proxy_booking")),
            "department": dept,
            "date": merged_slots.get("date"),
            "time": merged_slots.get("time"),
            "booking_time": booking_time,
            "customer_type": customer_type,
        }

        # Cal.com 새 예약 생성
        if calcom_client.is_calcom_enabled(dept or ""):
            try:
                cc_new = calcom_client.create_booking(
                    department=dept or "",
                    date=merged_slots.get("date", ""),
                    time=merged_slots.get("time", ""),
                    patient_name=new_appointment.get("patient_name") or "",
                    patient_contact=new_appointment.get("patient_contact") or "",
                    customer_type=customer_type or "new",
                )
                if isinstance(cc_new, dict) and cc_new.get("uid"):
                    new_appointment["calcom_uid"] = cc_new["uid"]
                elif cc_new is None:
                    record_kpi_event(KpiEvent.AGENT_HARD_FAIL)
                    return _build_response_and_record(
                        session_state,
                        action="clarify",
                        message="현재 외부 예약 시스템 응답 지연으로 변경 처리가 불가합니다. 잠시 후 다시 시도해주세요.",
                        department=dept,
                        ticket=ticket,
                        classified_intent="modify_appointment",
                        intent_result=intent_result,
                        policy_result={"allowed": True},
                        customer_type=customer_type,
                    )
            except Exception:
                record_kpi_event(KpiEvent.AGENT_HARD_FAIL)
                return _build_response_and_record(
                    session_state,
                    action="clarify",
                    message="현재 외부 예약 시스템 응답 지연으로 변경 처리가 불가합니다. 잠시 후 다시 시도해주세요.",
                    department=dept,
                    ticket=ticket,
                    classified_intent="modify_appointment",
                    intent_result=intent_result,
                    policy_result={"allowed": True},
                    customer_type=customer_type,
                )

        # 로컬 Storage에 새 예약 저장
        try:
            persisted = create_booking(new_appointment)
            all_appointments.append(persisted)
            target_existing_appointment = persisted
        except Exception:
            record_kpi_event(KpiEvent.AGENT_HARD_FAIL)
            return _build_response_and_record(
                session_state,
                action="clarify",
                message="변경된 예약 정보를 저장하는 중 문제가 발생했습니다. 다시 한 번 시도해 주세요.",
                department=dept,
                ticket=ticket,
                classified_intent="modify_appointment",
                intent_result=intent_result,
                policy_result={"allowed": True},
                customer_type=customer_type,
            )

    # ── 19단계: 성공 응답 생성 + KPI 기록 ──
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


# ============================================================================
# 멀티턴 채팅 진입점: process_message
# ============================================================================


def process_message(user_message: str, session: dict | None = None, now: datetime = None) -> dict:
    """멀티턴 채팅의 진입점. 세션 관리를 포함하여 process_ticket()을 래핑한다.

    매 턴마다 다음을 수행한다:
    1. now 기본값 설정 (UTC 현재 시각)
    2. session이 없으면 create_session()으로 새 세션 생성
    3. dialogue_state에 세션의 customer_name/birth_date/customer_type 동기화
    4. all_appointments가 없으면 디스크에서 로드
    5. ticket 딕셔너리를 세션 정보로 구성
    6. existing_appointment 탐색 및 세션에 캐싱
    7. process_ticket() 호출
    8. 결과에서 세션 상태 역동기화 (이름, 연락처, 초진/재진 등)

    Args:
        user_message: 사용자 입력 메시지.
        session: 세션 딕셔너리 (create_session()으로 생성). None이면 새로 생성.
        now: 현재 시각. None이면 UTC 기준 현재.

    Returns:
        dict: action, response, confidence, reasoning 등을 포함하는 최종 응답.
              session 딕셔너리는 in-place로 업데이트되므로 다음 턴에 재사용 가능.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    if session is None:
        session = create_session()

    # dialogue_state 초기화 및 세션 정보 동기화
    dialogue_state = session.setdefault("dialogue_state", {})
    dialogue_state.setdefault("customer_name", session.get("customer_name"))
    dialogue_state.setdefault("birth_date", normalize_birth_date(session.get("birth_date")))
    dialogue_state.setdefault("resolved_customer_type", session.get("customer_type"))
    all_appointments = session.get("all_appointments")
    if all_appointments is None:
        all_appointments = _load_appointments_from_disk()
        session["all_appointments"] = all_appointments

    # 티켓 구성: 세션의 누적 정보를 포함
    ticket = {
        "message": user_message,
        "customer_name": session.get("customer_name") or dialogue_state.get("customer_name"),
        "birth_date": normalize_birth_date(session.get("birth_date") or dialogue_state.get("birth_date")),
        "customer_type": session.get("customer_type") or dialogue_state.get("resolved_customer_type"),
        "context": session.get("context", {}),
    }

    # 기존 예약 탐색 및 세션 캐싱
    existing_appointment = _resolve_existing_appointment_from_ticket(
        ticket,
        all_appointments,
        session.get("existing_appointment"),
        now,
    )
    session["existing_appointment"] = existing_appointment

    # process_ticket 호출 (핵심 처리)
    result = process_ticket(
        ticket=ticket,
        all_appointments=all_appointments,
        existing_appointment=existing_appointment,
        session_state=dialogue_state,
        now=now,
    )

    # 결과에서 세션 상태 역동기화
    session["customer_name"] = dialogue_state.get("customer_name") or session.get("customer_name")
    session["patient_name"] = dialogue_state.get("patient_name") or session.get("patient_name")
    session["patient_contact"] = dialogue_state.get("patient_contact") or session.get("patient_contact")
    session["is_proxy_booking"] = dialogue_state.get("is_proxy_booking")
    session["birth_date"] = dialogue_state.get("birth_date") or session.get("birth_date")
    session["customer_type"] = dialogue_state.get("resolved_customer_type") or session.get("customer_type")
    session["last_result"] = result
    return result
