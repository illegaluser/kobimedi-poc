from datetime import datetime, timezone
from unittest.mock import patch

from src.agent import process_ticket


REFERENCE_NOW = datetime(2026, 3, 24, 2, 0, tzinfo=timezone.utc)
SAFE_RESULT = {
    "category": "safe",
    "department_hint": None,
    "mixed_department_guidance": False,
    "unsupported_department": None,
    "unsupported_doctor": None,
}


@patch("src.agent.apply_policy")
@patch("src.agent.classify_intent")
@patch("src.agent.classify_safety")
def test_F012_multiturn_clarify_accumulates_slots(
    mock_classify_safety,
    mock_classify_intent,
    mock_apply_policy,
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
            "missing_info": ["date", "time"],
        },
    ]
    mock_apply_policy.return_value = {
        "allowed": True,
        "reason": "정책 검사를 통과했습니다.",
        "recommended_action": "book_appointment",
    }

    session_state = {}

    first_result = process_ticket(
        {
            "customer_name": "김민수",
            "customer_type": "재진",
            "message": "내일 2시 예약",
        },
        all_appointments=[],
        existing_appointment=None,
        session_state=session_state,
        now=REFERENCE_NOW,
    )

    assert first_result["action"] == "clarify"
    assert "어느 분과" in first_result["response"]
    assert session_state["accumulated_slots"] == {
        "date": "2026-03-25",
        "time": "14:00",
        "department": None,
    }

    second_result = process_ticket(
        {
            "customer_name": "김민수",
            "customer_type": "재진",
            "message": "내과요",
        },
        all_appointments=[],
        existing_appointment=None,
        session_state=session_state,
        now=REFERENCE_NOW,
    )

    assert second_result["action"] == "clarify"
    assert "예약할까요" in second_result["response"]
    assert "내과" in second_result["response"]
    assert session_state["pending_confirmation"] is not None
    assert mock_apply_policy.call_count == 1

    intent_payload = mock_apply_policy.call_args[0][0]
    assert intent_payload["action"] == "book_appointment"
    assert intent_payload["department"] == "내과"
    assert intent_payload["date"] == "2026-03-25"
    assert intent_payload["time"] == "14:00"


@patch("src.agent.apply_policy")
@patch("src.agent.classify_intent")
@patch("src.agent.classify_safety")
@patch("src.agent.create_booking")
def test_F013_two_step_confirmation_flow(
    mock_create_booking,
    mock_classify_safety,
    mock_classify_intent,
    mock_apply_policy,
):
    mock_classify_safety.return_value = SAFE_RESULT
    mock_create_booking.return_value = {
        "id": "booking-001",
        "customer_name": "김민수",
        "department": "내과",
        "date": "2026-03-25",
        "time": "14:00",
        "booking_time": "2026-03-25T14:00:00+00:00",
        "customer_type": "재진",
        "status": "active",
    }
    mock_classify_intent.return_value = {
        "action": "book_appointment",
        "department": "내과",
        "date": "2026-03-25",
        "time": "14:00",
        "missing_info": [],
    }
    mock_apply_policy.return_value = {
        "allowed": True,
        "reason": "정책 검사를 통과했습니다.",
        "recommended_action": "book_appointment",
    }

    session_state = {}

    proposal_result = process_ticket(
        {
            "customer_name": "김민수",
            "customer_type": "재진",
            "message": "내일 2시 내과 예약",
        },
        all_appointments=[],
        existing_appointment=None,
        session_state=session_state,
        now=REFERENCE_NOW,
    )

    assert proposal_result["action"] == "clarify"
    assert "예약할까요" in proposal_result["response"]
    assert "김만수 원장" in proposal_result["response"]
    assert session_state["pending_confirmation"] is not None

    mock_classify_safety.reset_mock()
    mock_classify_intent.reset_mock()
    mock_apply_policy.reset_mock()

    confirmed_result = process_ticket(
        {
            "customer_name": "김민수",
            "customer_type": "재진",
            "message": "네",
        },
        all_appointments=[],
        existing_appointment=None,
        session_state=session_state,
        now=REFERENCE_NOW,
    )

    assert confirmed_result["action"] == "book_appointment"
    assert "예약이 완료되었습니다" in confirmed_result["response"]
    assert session_state["pending_confirmation"] is None
    mock_create_booking.assert_called_once()
    mock_classify_safety.assert_not_called()
    mock_classify_intent.assert_not_called()
    mock_apply_policy.assert_not_called()


@patch("src.agent.apply_policy")
@patch("src.agent.classify_intent")
@patch("src.agent.classify_safety")
def test_F014_ambiguous_cancel_requires_choice_and_resolves(
    mock_classify_safety,
    mock_classify_intent,
    mock_apply_policy,
):
    mock_classify_safety.return_value = SAFE_RESULT
    mock_classify_intent.return_value = {
        "action": "clarify",
        "department": None,
        "date": None,
        "time": None,
        "missing_info": ["appointment_target"],
    }
    mock_apply_policy.return_value = {
        "allowed": True,
        "reason": "정책 검사를 통과했습니다.",
        "recommended_action": "cancel_appointment",
    }

    appointments = [
        {
            "id": "appt-1",
            "customer_name": "김민수",
            "department": "내과",
            "booking_time": "2026-03-27T14:00:00Z",
            "customer_type": "재진",
        },
        {
            "id": "appt-2",
            "customer_name": "김민수",
            "department": "정형외과",
            "booking_time": "2026-03-29T14:00:00Z",
            "customer_type": "재진",
        },
    ]
    session_state = {}

    clarify_result = process_ticket(
        {
            "customer_name": "김민수",
            "customer_type": "재진",
            "message": "예약 취소해주세요",
        },
        all_appointments=appointments,
        existing_appointment=None,
        session_state=session_state,
        now=REFERENCE_NOW,
    )

    assert clarify_result["action"] == "clarify"
    assert "어떤 예약인지 선택해주세요" in clarify_result["response"]
    assert "1)" in clarify_result["response"]
    assert "2)" in clarify_result["response"]
    assert len(session_state["pending_candidates"]) == 2

    mock_classify_safety.reset_mock()
    mock_classify_intent.reset_mock()

    resolved_result = process_ticket(
        {
            "customer_name": "김민수",
            "customer_type": "재진",
            "message": "2번이요",
        },
        all_appointments=appointments,
        existing_appointment=None,
        session_state=session_state,
        now=REFERENCE_NOW,
    )

    assert resolved_result["action"] == "cancel_appointment"
    assert "예약 취소가 완료되었습니다" in resolved_result["response"]
    assert "정형외과" in resolved_result["response"]
    assert session_state["pending_candidates"] is None
    mock_classify_safety.assert_not_called()
    mock_classify_intent.assert_not_called()
    assert mock_apply_policy.call_args[0][1]["id"] == "appt-2"


@patch("src.agent.apply_policy")
@patch("src.agent.classify_intent")
@patch("src.agent.classify_safety")
def test_batch_like_call_without_session_state_remains_single_turn(
    mock_classify_safety,
    mock_classify_intent,
    mock_apply_policy,
):
    mock_classify_safety.return_value = SAFE_RESULT
    mock_classify_intent.return_value = {
        "action": "book_appointment",
        "department": "내과",
        "date": "2026-03-25",
        "time": "14:00",
        "missing_info": [],
    }
    mock_apply_policy.return_value = {
        "allowed": True,
        "reason": "정책 검사를 통과했습니다.",
        "recommended_action": "book_appointment",
    }

    result = process_ticket(
        {
            "customer_name": "김민수",
            "customer_type": "재진",
            "message": "내일 2시 내과 예약",
        },
        all_appointments=[],
        existing_appointment=None,
        session_state=None,
        now=REFERENCE_NOW,
    )

    assert result["action"] == "clarify"
    assert "예약할까요" in result["response"]
    assert result["classified_intent"] == "book_appointment"


@patch("src.agent.resolve_customer_type_from_history")
@patch("src.agent.apply_policy")
@patch("src.agent.classify_intent")
@patch("src.agent.classify_safety")
def test_booking_flow_collects_name_then_birth_date_before_confirmation(
    mock_classify_safety,
    mock_classify_intent,
    mock_apply_policy,
    mock_resolve_customer_type,
):
    mock_classify_safety.return_value = SAFE_RESULT
    mock_classify_intent.return_value = {
        "action": "book_appointment",
        "department": "내과",
        "date": "2026-03-25",
        "time": "14:00",
        "missing_info": [],
    }
    mock_apply_policy.return_value = {
        "allowed": True,
        "reason": "정책 검사를 통과했습니다.",
        "recommended_action": "book_appointment",
    }
    mock_resolve_customer_type.side_effect = [
        {
            "customer_type": None,
            "ambiguous": True,
            "birth_date_candidates": ["1988-01-01", "1990-02-02"],
            "matched_bookings": [],
            "has_non_cancelled_history": False,
            "has_cancelled_history": False,
        },
        {
            "customer_type": "재진",
            "ambiguous": False,
            "birth_date_candidates": ["1988-01-01", "1990-02-02"],
            "matched_bookings": [{"id": "booking-001"}],
            "has_non_cancelled_history": True,
            "has_cancelled_history": False,
        },
    ]

    session_state = {}

    first_result = process_ticket(
        {
            "message": "내일 2시 내과 예약",
        },
        all_appointments=[],
        existing_appointment=None,
        session_state=session_state,
        now=REFERENCE_NOW,
    )

    assert first_result["action"] == "clarify"
    assert "성함" in first_result["response"]
    assert session_state["pending_missing_info"] == ["customer_name"]

    second_result = process_ticket(
        {
            "message": "김민수",
        },
        all_appointments=[],
        existing_appointment=None,
        session_state=session_state,
        now=REFERENCE_NOW,
    )

    assert second_result["action"] == "clarify"
    assert "생년월일" in second_result["response"]
    assert session_state["customer_name"] == "김민수"
    assert session_state["pending_missing_info"] == ["birth_date"]

    third_result = process_ticket(
        {
            "message": "1990-02-02",
        },
        all_appointments=[],
        existing_appointment=None,
        session_state=session_state,
        now=REFERENCE_NOW,
    )

    assert third_result["action"] == "clarify"
    assert "예약할까요" in third_result["response"]
    assert "내과" in third_result["response"]
    assert session_state["birth_date"] == "1990-02-02"
    assert session_state["resolved_customer_type"] == "재진"
    assert session_state["pending_confirmation"]["appointment"]["customer_type"] == "재진"
    assert session_state["pending_confirmation"]["appointment"]["birth_date"] == "1990-02-02"
    mock_classify_intent.assert_called_once()
