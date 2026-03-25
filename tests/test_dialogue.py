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


def _book_intent(department="내과", date="2026-03-25", time="14:00", **extra):
    return {
        "action": "book_appointment",
        "department": department,
        "date": date,
        "time": time,
        "missing_info": [],
        **extra,
    }


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
    mock_apply_policy.return_value = {
        "allowed": True,
        "reason": "정책 검사를 통과했습니다.",
        "recommended_action": "book_appointment",
    }
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
    mock_apply_policy.return_value = {
        "allowed": True,
        "reason": "정책 검사를 통과했습니다.",
        "recommended_action": "book_appointment",
    }
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
    mock_apply_policy.return_value = {
        "allowed": True,
        "reason": "정책 검사를 통과했습니다.",
        "recommended_action": "book_appointment",
    }
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
    assert "성함과 연락처" in first_result["response"]
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
    mock_apply_policy.return_value = {
        "allowed": True,
        "reason": "정책 검사를 통과했습니다.",
        "recommended_action": "book_appointment",
    }
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
    intent_payload = mock_apply_policy.call_args[0][0]
    assert intent_payload["department"] == "내과"
    assert intent_payload["date"] == "2026-03-25"
    assert intent_payload["time"] == "14:00"


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
    mock_apply_policy.return_value = {
        "allowed": True,
        "reason": "정책 검사를 통과했습니다.",
        "recommended_action": "book_appointment",
    }
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
        {
            "allowed": False,
            "reason": "요청하신 시간에는 예약이 이미 가득 찼습니다. 다른 시간을 선택해 주세요.",
            "recommended_action": "clarify",
            "alternative_slots": [
                "2026-03-25T14:30:00+00:00",
                "2026-03-25T15:00:00+00:00",
            ],
        },
        {
            "allowed": True,
            "reason": "정책 검사를 통과했습니다.",
            "recommended_action": "book_appointment",
        },
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
    mock_apply_policy.return_value = {
        "allowed": True,
        "reason": "정책 검사를 통과했습니다.",
        "recommended_action": "book_appointment",
    }
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
    mock_apply_policy.return_value = {
        "allowed": True,
        "reason": "정책 검사를 통과했습니다.",
        "recommended_action": "book_appointment",
    }
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


def test_F048_chat_and_run_share_agent_core_by_import_contract():
    from chat import create_session as chat_create_session  # noqa: PLC0415
    from chat import process_message as chat_process_message  # noqa: PLC0415
    from run import process_ticket as run_process_ticket  # noqa: PLC0415
    from src.agent import create_session, process_message, process_ticket  # noqa: PLC0415

    assert chat_create_session is create_session
    assert chat_process_message is process_message
    assert run_process_ticket is process_ticket
