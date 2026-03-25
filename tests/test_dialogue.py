from datetime import datetime, timezone
from unittest.mock import patch

from src.agent import (
    _extract_patient_contact,
    _extract_patient_name,
    process_ticket,
)
from src.models import Action, PolicyResult


REFERENCE_NOW = datetime(2026, 3, 24, 2, 0, tzinfo=timezone.utc)
SAFE_RESULT = {
    "category": "safe",
    "department_hint": None,
    "mixed_department_guidance": False,
    "unsupported_department": None,
    "unsupported_doctor": None,
}


def _book_intent(department="내과", date="2026-03-25", time="14:00", **extra):
    return {
        "action": "book_appointment",
        "department": department,
        "date": date,
        "time": time,
        "missing_info": [],
        **extra,
    }


# ---------------------------------------------------------------------------
# F-031: proxy question must be the FIRST thing asked in chat booking
# ---------------------------------------------------------------------------

@patch("src.agent.resolve_customer_type_from_history")
@patch("src.agent.apply_policy")
@patch("src.agent.classify_intent")
@patch("src.agent.classify_safety")
def test_F031_proxy_question_is_first_for_chat_booking(
    mock_classify_safety,
    mock_classify_intent,
    mock_apply_policy,
    mock_resolve_customer_type,
):
    mock_classify_safety.return_value = SAFE_RESULT
    mock_classify_intent.return_value = _book_intent()
    mock_apply_policy.return_value = PolicyResult(action=Action.BOOK_APPOINTMENT)
    mock_resolve_customer_type.return_value = {
        "customer_type": "재진",
        "ambiguous": False,
        "birth_date_candidates": [],
        "matched_bookings": [],
        "has_non_cancelled_history": True,
        "has_cancelled_history": False,
    }

    session_state = {}

    result = process_ticket(
        {
            "customer_name": "김민수",
            "customer_type": "재진",
            "message": "내일 2시 내과 예약하고 싶어요",
        },
        all_appointments=[],
        existing_appointment=None,
        session_state=session_state,
        now=REFERENCE_NOW,
    )

    assert result["action"] == "clarify"
    assert "본인이신가요" in result["response"]
    assert session_state["pending_missing_info"][0] == "is_proxy_booking"
    assert session_state["accumulated_slots"] == {
        "date": "2026-03-25",
        "time": "14:00",
        "department": "내과",
    }
    mock_apply_policy.assert_not_called()


# ---------------------------------------------------------------------------
# F-032: self-booking collects contact → confirmation
# ---------------------------------------------------------------------------

@patch("src.agent.resolve_customer_type_from_history")
@patch("src.agent.apply_policy")
@patch("src.agent.classify_intent")
@patch("src.agent.classify_safety")
def test_F032_self_booking_collects_patient_contact_then_confirms(
    mock_classify_safety,
    mock_classify_intent,
    mock_apply_policy,
    mock_resolve_customer_type,
):
    mock_classify_safety.return_value = SAFE_RESULT
    mock_classify_intent.return_value = _book_intent()
    mock_apply_policy.return_value = PolicyResult(action=Action.BOOK_APPOINTMENT)
    mock_resolve_customer_type.return_value = {
        "customer_type": "재진",
        "ambiguous": False,
        "birth_date_candidates": [],
        "matched_bookings": [{"id": "booking-001"}],
        "has_non_cancelled_history": True,
        "has_cancelled_history": False,
    }

    session_state = {}

    first_result = process_ticket(
        {
            "customer_name": "김민수",
            "customer_type": "재진",
            "message": "내일 2시 내과 예약하고 싶어요",
        },
        all_appointments=[],
        existing_appointment=None,
        session_state=session_state,
        now=REFERENCE_NOW,
    )
    assert first_result["action"] == "clarify"
    assert "본인이신가요" in first_result["response"]

    second_result = process_ticket(
        {"message": "본인이에요"},
        all_appointments=[],
        existing_appointment=None,
        session_state=session_state,
        now=REFERENCE_NOW,
    )
    assert second_result["action"] == "clarify"
    assert "연락처" in second_result["response"]
    assert session_state["is_proxy_booking"] is False
    assert session_state["patient_name"] == "김민수"

    third_result = process_ticket(
        {"message": "010-1234-5678"},
        all_appointments=[],
        existing_appointment=None,
        session_state=session_state,
        now=REFERENCE_NOW,
    )
    assert third_result["action"] == "clarify"
    assert "예약할까요" in third_result["response"]
    assert session_state["resolved_customer_type"] == "재진"
    assert session_state["pending_confirmation"]["appointment"]["patient_name"] == "김민수"
    assert session_state["pending_confirmation"]["appointment"]["patient_contact"] == "010-1234-5678"
    assert session_state["pending_confirmation"]["appointment"]["is_proxy_booking"] is False
    assert mock_apply_policy.call_count == 1


# ---------------------------------------------------------------------------
# F-033: proxy booking collects actual patient info
# ---------------------------------------------------------------------------

@patch("src.agent.resolve_customer_type_from_history")
@patch("src.agent.apply_policy")
@patch("src.agent.classify_intent")
@patch("src.agent.classify_safety")
def test_F033_proxy_booking_collects_actual_patient_info(
    mock_classify_safety,
    mock_classify_intent,
    mock_apply_policy,
    mock_resolve_customer_type,
):
    mock_classify_safety.return_value = SAFE_RESULT
    mock_classify_intent.return_value = _book_intent(is_proxy_booking=True)
    mock_apply_policy.return_value = PolicyResult(action=Action.BOOK_APPOINTMENT)
    mock_resolve_customer_type.return_value = {
        "customer_type": "초진",
        "ambiguous": False,
        "birth_date_candidates": [],
        "matched_bookings": [],
        "has_non_cancelled_history": False,
        "has_cancelled_history": False,
    }

    session_state = {}

    first_result = process_ticket(
        {
            "customer_name": "보호자",
            "message": "엄마 대신 내일 2시 내과 예약하고 싶어요",
        },
        all_appointments=[],
        existing_appointment=None,
        session_state=session_state,
        now=REFERENCE_NOW,
    )
    assert first_result["action"] == "clarify"
    assert "성함" in first_result["response"]
    assert session_state["is_proxy_booking"] is True

    second_result = process_ticket(
        {"message": "환자 이름은 김영희"},
        all_appointments=[],
        existing_appointment=None,
        session_state=session_state,
        now=REFERENCE_NOW,
    )
    assert second_result["action"] == "clarify"
    assert "연락처" in second_result["response"]
    assert session_state["patient_name"] == "김영희"

    third_result = process_ticket(
        {"message": "010-9999-8888"},
        all_appointments=[],
        existing_appointment=None,
        session_state=session_state,
        now=REFERENCE_NOW,
    )
    assert third_result["action"] == "clarify"
    assert "예약할까요" in third_result["response"]
    appointment = session_state["pending_confirmation"]["appointment"]
    assert appointment["patient_name"] == "김영희"
    assert appointment["patient_contact"] == "010-9999-8888"
    assert appointment["is_proxy_booking"] is True
    assert appointment["customer_type"] == "초진"


# ---------------------------------------------------------------------------
# F-041 / F-043 / F-044: pending queue and slots persist across turns
# ---------------------------------------------------------------------------

@patch("src.agent.resolve_customer_type_from_history")
@patch("src.agent.apply_policy")
@patch("src.agent.classify_intent")
@patch("src.agent.classify_safety")
def test_F041_F043_F044_pending_queue_and_slots_persist_across_turns(
    mock_classify_safety,
    mock_classify_intent,
    mock_apply_policy,
    mock_resolve_customer_type,
):
    mock_classify_safety.return_value = SAFE_RESULT
    mock_classify_intent.side_effect = [
        {
            "action": "clarify",
            "department": None,
            "date": "2026-03-25",
            "time": "14:00",
            "missing_info": ["department"],
        },
        {
            "action": "clarify",
            "department": "내과",
            "date": None,
            "time": None,
            "missing_info": [],
        },
    ]
    mock_apply_policy.return_value = PolicyResult(action=Action.BOOK_APPOINTMENT)
    mock_resolve_customer_type.return_value = {
        "customer_type": "재진",
        "ambiguous": False,
        "birth_date_candidates": [],
        "matched_bookings": [],
        "has_non_cancelled_history": True,
        "has_cancelled_history": False,
    }

    session_state = {}

    first_result = process_ticket(
        {
            "customer_name": "김민수",
            "message": "내일 2시 예약",
        },
        all_appointments=[],
        existing_appointment=None,
        session_state=session_state,
        now=REFERENCE_NOW,
    )
    assert first_result["action"] == "clarify"
    assert session_state["accumulated_slots"] == {
        "date": "2026-03-25",
        "time": "14:00",
        "department": None,
    }

    process_ticket(
        {"message": "본인이에요"},
        all_appointments=[],
        existing_appointment=None,
        session_state=session_state,
        now=REFERENCE_NOW,
    )
    process_ticket(
        {"message": "010-2222-3333"},
        all_appointments=[],
        existing_appointment=None,
        session_state=session_state,
        now=REFERENCE_NOW,
    )

    final_result = process_ticket(
        {"message": "내과요"},
        all_appointments=[],
        existing_appointment=None,
        session_state=session_state,
        now=REFERENCE_NOW,
    )
    assert final_result["action"] == "clarify"
    assert "예약할까요" in final_result["response"]
    assert session_state["accumulated_slots"] == {
        "date": "2026-03-25",
        "time": "14:00",
        "department": "내과",
    }
    # Verify apply_policy was called with the correct booking intent (PolicyTicket object)
    policy_call_arg = mock_apply_policy.call_args[0][0]
    assert policy_call_arg.intent == "book_appointment"
    booking_time_str = str(policy_call_arg.context.get("appointment_time") or "")
    assert "2026-03-25" in booking_time_str
    assert "14:00" in booking_time_str


# ---------------------------------------------------------------------------
# F-042: clarify turn count escalates after 4 failed proxy-question answers
# ---------------------------------------------------------------------------

@patch("src.agent.resolve_customer_type_from_history")
@patch("src.agent.apply_policy")
@patch("src.agent.classify_intent")
@patch("src.agent.classify_safety")
def test_F042_clarify_turn_limit_escalates_after_four_turns(
    mock_classify_safety,
    mock_classify_intent,
    mock_apply_policy,
    mock_resolve_customer_type,
):
    mock_classify_safety.return_value = SAFE_RESULT
    mock_classify_intent.return_value = _book_intent()
    mock_apply_policy.return_value = PolicyResult(action=Action.BOOK_APPOINTMENT)
    mock_resolve_customer_type.return_value = {
        "customer_type": "재진",
        "ambiguous": False,
        "birth_date_candidates": [],
        "matched_bookings": [],
        "has_non_cancelled_history": True,
        "has_cancelled_history": False,
    }

    session_state = {}

    first_result = process_ticket(
        {
            "customer_name": "김민수",
            "message": "내일 2시 내과 예약하고 싶어요",
        },
        all_appointments=[],
        existing_appointment=None,
        session_state=session_state,
        now=REFERENCE_NOW,
    )
    assert first_result["action"] == "clarify"

    second_result = process_ticket(
        {"message": "모르겠어요"},
        all_appointments=[],
        existing_appointment=None,
        session_state=session_state,
        now=REFERENCE_NOW,
    )
    third_result = process_ticket(
        {"message": "잘 모르겠어요"},
        all_appointments=[],
        existing_appointment=None,
        session_state=session_state,
        now=REFERENCE_NOW,
    )
    fourth_result = process_ticket(
        {"message": "대답하기 어려워요"},
        all_appointments=[],
        existing_appointment=None,
        session_state=session_state,
        now=REFERENCE_NOW,
    )

    assert second_result["action"] == "clarify"
    assert third_result["action"] == "clarify"
    assert fourth_result["action"] == "escalate"
    assert session_state["clarify_turn_count"] >= 4


# ---------------------------------------------------------------------------
# F-045: alternative slot selection flows to confirmation
# ---------------------------------------------------------------------------

@patch("src.agent.resolve_customer_type_from_history")
@patch("src.agent.apply_policy")
@patch("src.agent.classify_intent")
@patch("src.agent.classify_safety")
def test_F045_alternative_slot_selection_flows_to_confirmation(
    mock_classify_safety,
    mock_classify_intent,
    mock_apply_policy,
    mock_resolve_customer_type,
):
    mock_classify_safety.return_value = SAFE_RESULT
    mock_classify_intent.side_effect = [
        _book_intent(),
        {"action": "clarify", "department": None, "date": None, "time": None, "missing_info": []},
    ]
    mock_apply_policy.side_effect = [
        PolicyResult(
            action=Action.CLARIFY,
            message="요청하신 시간에는 예약이 이미 가득 찼습니다. 다른 시간을 선택해 주세요.",
            suggested_slots=[
                "2026-03-25T14:30:00+00:00",
                "2026-03-25T15:00:00+00:00",
            ],
        ),
        PolicyResult(action=Action.BOOK_APPOINTMENT),
    ]
    mock_resolve_customer_type.return_value = {
        "customer_type": "재진",
        "ambiguous": False,
        "birth_date_candidates": [],
        "matched_bookings": [],
        "has_non_cancelled_history": True,
        "has_cancelled_history": False,
    }

    session_state = {
        "customer_name": "김민수",
        "patient_name": "김민수",
        "patient_contact": "010-1111-2222",
        "is_proxy_booking": False,
    }

    first_result = process_ticket(
        {
            "customer_name": "김민수",
            "patient_name": "김민수",
            "patient_contact": "010-1111-2222",
            "is_proxy_booking": False,
            "message": "내일 2시 내과 예약하고 싶어요",
        },
        all_appointments=[],
        existing_appointment=None,
        session_state=session_state,
        now=REFERENCE_NOW,
    )
    assert first_result["action"] == "clarify"
    assert "가능한 다른 시간" in first_result["response"]
    assert session_state["pending_alternative_slots"] is not None

    second_result = process_ticket(
        {"message": "2번이요"},
        all_appointments=[],
        existing_appointment=None,
        session_state=session_state,
        now=REFERENCE_NOW,
    )
    assert second_result["action"] == "clarify"
    assert "예약할까요" in second_result["response"]
    appointment = session_state["pending_confirmation"]["appointment"]
    assert appointment["date"] == "2026-03-25"
    assert appointment["time"] == "15:00"


# ---------------------------------------------------------------------------
# F-046: persists only after final confirmation
# ---------------------------------------------------------------------------

@patch("src.agent.resolve_customer_type_from_history")
@patch("src.agent.apply_policy")
@patch("src.agent.classify_intent")
@patch("src.agent.classify_safety")
@patch("src.agent.create_booking")
def test_F046_persists_only_after_final_confirmation(
    mock_create_booking,
    mock_classify_safety,
    mock_classify_intent,
    mock_apply_policy,
    mock_resolve_customer_type,
):
    mock_classify_safety.return_value = SAFE_RESULT
    mock_classify_intent.return_value = _book_intent()
    mock_apply_policy.return_value = PolicyResult(action=Action.BOOK_APPOINTMENT)
    mock_resolve_customer_type.return_value = {
        "customer_type": "재진",
        "ambiguous": False,
        "birth_date_candidates": [],
        "matched_bookings": [],
        "has_non_cancelled_history": True,
        "has_cancelled_history": False,
    }
    mock_create_booking.return_value = {
        "id": "booking-001",
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

    session_state = {}

    process_ticket(
        {
            "customer_name": "김민수",
            "message": "내일 2시 내과 예약하고 싶어요",
        },
        all_appointments=[],
        existing_appointment=None,
        session_state=session_state,
        now=REFERENCE_NOW,
    )
    process_ticket(
        {"message": "본인이에요"},
        all_appointments=[],
        existing_appointment=None,
        session_state=session_state,
        now=REFERENCE_NOW,
    )
    proposal_result = process_ticket(
        {"message": "010-1234-5678"},
        all_appointments=[],
        existing_appointment=None,
        session_state=session_state,
        now=REFERENCE_NOW,
    )

    assert proposal_result["action"] == "clarify"
    assert session_state["pending_confirmation"] is not None
    mock_create_booking.assert_not_called()

    confirmed_result = process_ticket(
        {"message": "네"},
        all_appointments=[],
        existing_appointment=None,
        session_state=session_state,
        now=REFERENCE_NOW,
    )
    assert confirmed_result["action"] == "book_appointment"
    assert "예약이 완료되었습니다" in confirmed_result["response"]
    mock_create_booking.assert_called_once()


# ---------------------------------------------------------------------------
# F-042: clarify_turn_count resets when valid information is provided
# ---------------------------------------------------------------------------

@patch("src.agent.resolve_customer_type_from_history")
@patch("src.agent.apply_policy")
@patch("src.agent.classify_intent")
@patch("src.agent.classify_safety")
def test_F042_clarify_turn_count_resets_on_progress(
    mock_classify_safety,
    mock_classify_intent,
    mock_apply_policy,
    mock_resolve_customer_type,
):
    mock_classify_safety.return_value = SAFE_RESULT
    # 정보가 점진적으로 채워지는 시나리오 모의
    mock_classify_intent.side_effect = [
        {"action": "clarify", "department": None, "date": None, "time": None, "missing_info": ["is_proxy_booking"]},
        {"action": "clarify", "department": None, "date": None, "time": None, "missing_info": ["patient_name", "patient_contact"]},
        {"action": "clarify", "department": None, "date": "2026-03-31", "time": None, "missing_info": ["time"]},
    ]
    mock_apply_policy.return_value = PolicyResult(action=Action.BOOK_APPOINTMENT)
    mock_resolve_customer_type.return_value = {
        "customer_type": "재진", "ambiguous": False, "matched_bookings": [],
        "has_non_cancelled_history": True, "has_cancelled_history": False,
    }

    session_state = {}

    # Turn 1: "예약할래요" → action inferred as book_appointment → proxy question
    first_result = process_ticket(
        {"message": "예약할래요"},
        all_appointments=[], existing_appointment=None,
        session_state=session_state, now=REFERENCE_NOW,
    )
    assert first_result["action"] == "clarify"
    assert session_state.get("clarify_turn_count", 0) == 1

    # Turn 2: "본인입니다." → proxy consumed, count reset then re-incremented for next question
    second_result = process_ticket(
        {"message": "본인입니다."},
        all_appointments=[], existing_appointment=None,
        session_state=session_state, now=REFERENCE_NOW,
    )
    assert second_result["action"] == "clarify"
    # 진전이 있었으므로 카운트는 최대 1 (리셋 후 다음 질문으로 인한 1회 증가)
    assert session_state.get("clarify_turn_count", 0) <= 1

    # Turn 3: "이경석, 010-2938-4744" → 이름+연락처 동시 추출, count reset
    third_result = process_ticket(
        {"message": "이경석, 010-2938-4744"},
        all_appointments=[], existing_appointment=None,
        session_state=session_state, now=REFERENCE_NOW,
    )
    assert third_result["action"] == "clarify"
    # 이름+연락처가 동시에 소비되었으므로 카운트는 리셋 상태(0) — 절대 에스컬레이트 없음
    assert session_state.get("clarify_turn_count", 0) <= 1
    assert third_result["action"] != "escalate"
    # 이름과 연락처가 올바르게 추출되어 세션에 저장됨
    assert session_state.get("patient_name") == "이경석"
    assert session_state.get("patient_contact") == "010-2938-4744"


# ---------------------------------------------------------------------------
# F-047: batch mode skips proxy question and directly shows confirmation
# ---------------------------------------------------------------------------

@patch("src.agent.resolve_customer_type_from_history")
@patch("src.agent.apply_policy")
@patch("src.agent.classify_intent")
@patch("src.agent.classify_safety")
def test_F047_batch_mode_does_not_force_proxy_question_and_uses_single_turn(
    mock_classify_safety,
    mock_classify_intent,
    mock_apply_policy,
    mock_resolve_customer_type,
):
    mock_classify_safety.return_value = SAFE_RESULT
    mock_classify_intent.return_value = _book_intent()
    mock_apply_policy.return_value = PolicyResult(action=Action.BOOK_APPOINTMENT)
    mock_resolve_customer_type.return_value = {
        "customer_type": "재진",
        "ambiguous": False,
        "birth_date_candidates": [],
        "matched_bookings": [],
        "has_non_cancelled_history": True,
        "has_cancelled_history": False,
    }

    result = process_ticket(
        {
            "customer_name": "김민수",
            "customer_type": "재진",
            "message": "내일 2시 내과 예약하고 싶어요",
        },
        all_appointments=[],
        existing_appointment=None,
        session_state=None,
        now=REFERENCE_NOW,
    )

    assert result["action"] == "clarify"
    assert "본인이신가요" not in result["response"]
    assert "예약할까요" in result["response"]


# ---------------------------------------------------------------------------
# F-048: chat.py and run.py share the same agent core
# ---------------------------------------------------------------------------

def test_F048_chat_and_run_share_agent_core_by_import_contract():
    from chat import create_session as chat_create_session  # noqa: PLC0415
    from chat import process_message as chat_process_message  # noqa: PLC0415
    from run import process_ticket as run_process_ticket  # noqa: PLC0415
    from src.agent import create_session, process_message, process_ticket  # noqa: PLC0415

    assert chat_create_session is create_session
    assert chat_process_message is process_message
    assert run_process_ticket is process_ticket


# ---------------------------------------------------------------------------
# F-049: name + contact extracted simultaneously from a single message
# ---------------------------------------------------------------------------

def test_F049_extract_patient_name_from_mixed_input():
    """이름 추출 함수는 전화번호가 섞인 문장에서도 이름만 정확히 추출해야 한다 (re.search 기반)."""
    # 이름+연락처 콤마 구분
    assert _extract_patient_name("이경석, 010-2938-4744") == "이경석"
    # 이름+연락처 공백 구분
    assert _extract_patient_name("김영희 010-1111-2222") == "김영희"
    # 이름만 (기준선)
    assert _extract_patient_name("이경석") == "이경석"
    # "이름은" 패턴
    assert _extract_patient_name("환자 이름은 박지수예요") == "박지수"
    # 전화번호만 → None
    assert _extract_patient_name("010-2938-4744") is None


def test_F049_extract_patient_contact_from_mixed_input():
    """전화번호 추출은 이름이 함께 있어도 정상 동작해야 한다."""
    assert _extract_patient_contact("이경석, 010-2938-4744") == "010-2938-4744"
    assert _extract_patient_contact("김영희 010-1111-2222") == "010-1111-2222"
    assert _extract_patient_contact("010-9999-8888") == "010-9999-8888"
    assert _extract_patient_contact("이경석") is None


@patch("src.agent.resolve_customer_type_from_history")
@patch("src.agent.apply_policy")
@patch("src.agent.classify_intent")
@patch("src.agent.classify_safety")
def test_F049_simultaneous_name_contact_consumed_in_one_turn(
    mock_classify_safety,
    mock_classify_intent,
    mock_apply_policy,
    mock_resolve_customer_type,
):
    """'이경석, 010-2938-4744' 입력 시 patient_name과 patient_contact가 한 턴에 동시 수집된다."""
    mock_classify_safety.return_value = SAFE_RESULT
    mock_classify_intent.return_value = _book_intent()
    mock_apply_policy.return_value = PolicyResult(action=Action.BOOK_APPOINTMENT)
    mock_resolve_customer_type.return_value = {
        "customer_type": "신규",
        "ambiguous": False,
        "birth_date_candidates": [],
        "matched_bookings": [],
        "has_non_cancelled_history": False,
        "has_cancelled_history": False,
    }

    # 세션 준비: 이미 is_proxy_booking=False가 확인된 상태, 이름+연락처 미입력
    session_state = {
        "is_proxy_booking": False,
        "pending_missing_info": ["patient_name", "patient_contact"],
        "pending_missing_info_queue": ["patient_name", "patient_contact"],
        "pending_action": "book_appointment",
        "accumulated_slots": {"department": "내과", "date": "2026-03-25", "time": "14:00"},
    }

    result = process_ticket(
        {"message": "이경석, 010-2938-4744"},
        all_appointments=[],
        existing_appointment=None,
        session_state=session_state,
        now=REFERENCE_NOW,
    )

    # 두 필드가 동시에 소비되었으므로 더 이상 같은 정보를 묻지 않음
    assert session_state.get("patient_name") == "이경석"
    assert session_state.get("patient_contact") == "010-2938-4744"
    # 이름+연락처 소비 → escalate 아님
    assert result["action"] != "escalate"
    # 카운트 리셋 확인
    assert session_state.get("clarify_turn_count", 0) == 0
