"""
tests/test_book_modify_cancel_flow.py — 예약 → 변경 → 취소 전체 플로우 E2E 테스트

다양한 구어체/격식체 발화로 book → modify → cancel 흐름을 검증한다.
최소 10개 이상의 시나리오를 parametrize로 구성하며, 각 시나리오는
채팅 모드(멀티턴)에서 다음을 검증한다:

1. 예약 완료 (book_appointment)
2. 예약 변경 요청 → 새 날짜/시간 수집 → 변경 완료 (modify_appointment)
3. 예약 취소 요청 → 취소 완료 (cancel_appointment)
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from src.agent import process_ticket
from src.models import Action, PolicyResult


# ─────────────────────────────────────────────────────────────
# 공통 fixtures / helpers
# ─────────────────────────────────────────────────────────────

# KST 2026-03-24 11:00 기준 (테스트 내 "다음주", "내일" 등이 해석될 기준)
REFERENCE_NOW = datetime(2026, 3, 24, 2, 0, tzinfo=timezone.utc)

SAFE_RESULT = {
    "category": "safe",
    "department_hint": None,
    "mixed_department_guidance": False,
    "unsupported_department": None,
    "unsupported_doctor": None,
}


def _make_intent_side_effect(
    action: str,
    department: str = "내과",
    date: str = "2026-03-25",
    time: str = "14:00",
    **extra,
):
    """classify_intent mock side_effect 생성. 메시지에서 이름·연락처·proxy 여부를 추출."""
    base = {
        "action": action,
        "department": department,
        "date": date,
        "time": time,
        "missing_info": [],
        **extra,
    }

    def _side_effect(message, *args, **kwargs):
        result = dict(base)
        # 전화번호 추출
        phone_match = re.search(r"01[0-9][- ]?\d{3,4}[- ]?\d{4}", message)
        if phone_match:
            raw = re.sub(r"[- ]", "", phone_match.group(0))
            result["patient_contact"] = f"{raw[:3]}-{raw[3:7]}-{raw[7:]}"
        # 이름 추출
        name_match = re.search(r"([가-힣]{2,4})\s+01[0-9]", message)
        if not name_match:
            name_match = re.search(r"([가-힣]{2,4}),?\s+01[0-9]", message)
        if name_match:
            result["patient_name"] = name_match.group(1)
        return result

    return _side_effect


def _resolve_revisit():
    return {
        "customer_type": "재진",
        "ambiguous": False,
        "birth_date_candidates": [],
        "matched_bookings": [{"id": "booking-001"}],
        "has_non_cancelled_history": True,
        "has_cancelled_history": False,
    }


BOOKED_APPOINTMENT = {
    "id": "b-flow-001",
    "customer_name": "김민수",
    "patient_name": "김민수",
    "patient_contact": "010-1234-5678",
    "is_proxy_booking": False,
    "department": "내과",
    "date": "2026-03-25",
    "time": "14:00",
    "booking_time": "2026-03-25T14:00:00+00:00",
    "customer_type": "재진",
    "status": "active",
}


def _run_book_phase(
    mock_safety, mock_intent, mock_policy, mock_resolve, mock_create_booking,
):
    """예약 생성 단계를 공통으로 실행하여 session_state를 반환한다."""
    mock_safety.return_value = SAFE_RESULT
    mock_intent.side_effect = _make_intent_side_effect("book_appointment")
    mock_policy.return_value = PolicyResult(action=Action.BOOK_APPOINTMENT)
    mock_resolve.return_value = _resolve_revisit()
    mock_create_booking.return_value = dict(BOOKED_APPOINTMENT)

    session_state = {}
    ticket_base = {"customer_name": "김민수", "customer_type": "재진"}

    # Turn 1: 예약 요청 → proxy 질문
    r1 = process_ticket(
        {**ticket_base, "message": "내일 2시 내과 예약하고 싶어요"},
        all_appointments=[], existing_appointment=None,
        session_state=session_state, now=REFERENCE_NOW,
    )
    assert r1["action"] == "clarify"
    assert "본인이신가요" in r1["response"]

    # Turn 2: 본인 → 연락처 질문
    r2 = process_ticket(
        {"message": "본인이에요"},
        all_appointments=[], existing_appointment=None,
        session_state=session_state, now=REFERENCE_NOW,
    )
    assert r2["action"] == "clarify"
    assert "연락처" in r2["response"] or "성함" in r2["response"]

    # Turn 3: 연락처 → 확인 질문
    r3 = process_ticket(
        {"message": "김민수 010-1234-5678"},
        all_appointments=[], existing_appointment=None,
        session_state=session_state, now=REFERENCE_NOW,
    )
    assert r3["action"] == "clarify"
    assert "예약할까요" in r3["response"]

    # Turn 4: "네" → 예약 확정
    r4 = process_ticket(
        {"message": "네"},
        all_appointments=[], existing_appointment=None,
        session_state=session_state, now=REFERENCE_NOW,
    )
    assert r4["action"] == "book_appointment"
    mock_create_booking.assert_called_once()

    return session_state


# ─────────────────────────────────────────────────────────────
# 테스트 시나리오 (예약 → 변경 → 취소)
# ─────────────────────────────────────────────────────────────

MODIFY_UTTERANCES = [
    # (변경 요청 발화, 새 날짜/시간 발화, 새 날짜, 새 시간, 설명)
    ("예약 변경할래요", "3월 27일 오후 3시로요", "2026-03-27", "15:00", "격식체 변경 + 한글 날짜"),
    ("예약 수정해주세요", "3/28 오전 10시", "2026-03-28", "10:00", "격식체 수정 + 숫자 날짜"),
    ("시간 바꿔줘", "3월 30일 오후 2시", "2026-03-30", "14:00", "구어체 바꿔줘 + 한글 날짜"),
    ("예약 옮겨주세요", "4/1 오전 11시", "2026-04-01", "11:00", "격식체 옮겨 + 숫자 날짜"),
    ("날짜를 변경하고 싶어요", "4월 2일 오후 4시", "2026-04-02", "16:00", "완곡체 변경 + 한글 날짜"),
]

CANCEL_UTTERANCES = [
    # (취소 요청 발화, 설명)
    ("예약 취소할게요", "격식체 취소"),
    ("예약 취소해주세요", "정중체 취소"),
    ("그 예약 빼줘", "구어체 빼줘"),
    ("안 갈래요", "구어체 안 갈래"),
    ("예약 취소 부탁드립니다", "매우 정중체 취소"),
]

# modify와 cancel을 조합해 최소 10개 시나리오 생성 (5 modify × 각각 대응 cancel)
SCENARIOS = []
for i, (mod_req, mod_slot, new_date, new_time, mod_desc) in enumerate(MODIFY_UTTERANCES):
    cancel_req, cancel_desc = CANCEL_UTTERANCES[i]
    SCENARIOS.append(
        pytest.param(
            mod_req, mod_slot, new_date, new_time,
            cancel_req,
            id=f"S{i+1:02d}_{mod_desc}_{cancel_desc}",
        )
    )

# 추가 5개: 같은 modify에 다른 cancel 조합
EXTRA_COMBOS = [
    (0, 2, "격식체변경+구어체빼줘"),
    (1, 3, "수정+안갈래"),
    (2, 4, "바꿔줘+정중체취소"),
    (3, 0, "옮겨+격식체취소"),
    (4, 1, "완곡변경+정중체취소"),
]
for mod_i, cancel_i, desc in EXTRA_COMBOS:
    mod_req, mod_slot, new_date, new_time, _ = MODIFY_UTTERANCES[mod_i]
    cancel_req, _ = CANCEL_UTTERANCES[cancel_i]
    SCENARIOS.append(
        pytest.param(
            mod_req, mod_slot, new_date, new_time,
            cancel_req,
            id=f"S{len(SCENARIOS)+1:02d}_{desc}",
        )
    )


@pytest.mark.parametrize("modify_request,modify_slot,new_date,new_time,cancel_request", SCENARIOS)
@patch("src.agent.create_booking")
@patch("src.agent.resolve_customer_type_from_history")
@patch("src.agent.apply_policy")
@patch("src.agent.classify_intent")
@patch("src.agent.classify_safety")
def test_book_modify_cancel_full_flow(
    mock_safety,
    mock_intent,
    mock_policy,
    mock_resolve,
    mock_create_booking,
    modify_request,
    modify_slot,
    new_date,
    new_time,
    cancel_request,
):
    """예약 → 변경 → 취소 전체 플로우를 다양한 발화로 검증한다."""

    # ── Phase 1: 예약 생성 ──
    session_state = _run_book_phase(
        mock_safety, mock_intent, mock_policy, mock_resolve, mock_create_booking,
    )

    booked = dict(BOOKED_APPOINTMENT)
    all_appointments = [booked]

    # ── Phase 2: 예약 변경 ──
    # 변경 요청 시 새 날짜/시간은 아직 미정이므로 None으로 설정
    mock_intent.side_effect = _make_intent_side_effect(
        "modify_appointment",
        department="내과",
        date=None,
        time=None,
    )
    mock_policy.return_value = PolicyResult(action=Action.MODIFY_APPOINTMENT)

    # 변경 요청 → identity 수집 (proxy → 본인 → 연락처) → 새 날짜/시간 → 완료
    r = process_ticket(
        {"message": modify_request},
        all_appointments=all_appointments,
        existing_appointment=booked,
        session_state=session_state,
        now=REFERENCE_NOW,
    )
    assert r["action"] == "clarify"

    # identity 수집 루프: proxy / 본인 / 연락처 / 날짜시간 질문을 모두 처리
    while r["action"] == "clarify":
        if "본인이신가요" in r["response"]:
            r = process_ticket(
                {"message": "본인"},
                all_appointments=all_appointments,
                existing_appointment=booked,
                session_state=session_state,
                now=REFERENCE_NOW,
            )
        elif "연락처" in r["response"] or "성함" in r["response"]:
            r = process_ticket(
                {"message": "김민수 010-1234-5678"},
                all_appointments=all_appointments,
                existing_appointment=booked,
                session_state=session_state,
                now=REFERENCE_NOW,
            )
        elif "날짜" in r["response"] or "시간" in r["response"] or "언제" in r["response"]:
            # 새 날짜/시간 intent로 교체
            mock_intent.side_effect = _make_intent_side_effect(
                "modify_appointment",
                department="내과",
                date=new_date,
                time=new_time,
            )
            r = process_ticket(
                {"message": modify_slot},
                all_appointments=all_appointments,
                existing_appointment=booked,
                session_state=session_state,
                now=REFERENCE_NOW,
            )
        else:
            # 예상치 못한 clarify — 무한루프 방지
            break

    assert r["action"] == "modify_appointment", (
        f"변경 완료 기대했으나 action={r['action']}, response={r['response']}"
    )
    assert "변경" in r["response"]

    # 예약 정보 업데이트
    booked["date"] = new_date
    booked["time"] = new_time
    booked["booking_time"] = f"{new_date}T{new_time}:00+00:00"

    # ── Phase 3: 예약 취소 ──
    mock_intent.side_effect = _make_intent_side_effect(
        "cancel_appointment",
        department="내과",
        date=new_date,
        time=new_time,
    )
    mock_policy.return_value = PolicyResult(action=Action.CANCEL_APPOINTMENT)

    # 취소 요청 → identity 수집 → 취소 완료
    r = process_ticket(
        {"message": cancel_request},
        all_appointments=all_appointments,
        existing_appointment=booked,
        session_state=session_state,
        now=REFERENCE_NOW,
    )

    # identity 수집 루프
    max_turns = 5
    while r["action"] == "clarify" and max_turns > 0:
        max_turns -= 1
        if "본인이신가요" in r["response"]:
            r = process_ticket(
                {"message": "본인"},
                all_appointments=all_appointments,
                existing_appointment=booked,
                session_state=session_state,
                now=REFERENCE_NOW,
            )
        elif "연락처" in r["response"] or "성함" in r["response"]:
            r = process_ticket(
                {"message": "김민수 010-1234-5678"},
                all_appointments=all_appointments,
                existing_appointment=booked,
                session_state=session_state,
                now=REFERENCE_NOW,
            )
        else:
            break

    assert r["action"] == "cancel_appointment", (
        f"취소 완료 기대했으나 action={r['action']}, response={r['response']}"
    )
    assert "취소" in r["response"]
