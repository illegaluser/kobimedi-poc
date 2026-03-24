from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import patch

import chat
import run
from src.agent import process_ticket


REFERENCE_NOW = datetime(2026, 3, 24, 2, 0, tzinfo=timezone.utc)
SAFE_RESULT = {
    "category": "safe",
    "department_hint": None,
    "mixed_department_guidance": False,
    "unsupported_department": None,
    "unsupported_doctor": None,
}
REQUIRED_RESULT_KEYS = {
    "ticket_id",
    "classified_intent",
    "department",
    "action",
    "response",
    "confidence",
    "reasoning",
}


@patch("chat.process_message")
@patch("chat.create_session")
def test_F021_chat_main_delegates_to_process_message(mock_create_session, mock_process_message, capsys):
    session = {"dialogue_state": {}}
    mock_create_session.return_value = session
    mock_process_message.return_value = {"response": "예약을 도와드릴게요."}

    with patch("builtins.input", side_effect=["내일 예약", "quit"]):
        chat.main()

    captured = capsys.readouterr()
    assert "🏥 코비메디 예약 챗봇입니다. 무엇을 도와드릴까요?" in captured.out
    assert "예약을 도와드릴게요." in captured.out
    mock_create_session.assert_called_once()
    mock_process_message.assert_called_once_with("내일 예약", session)


@patch("src.agent.apply_policy")
@patch("src.agent.classify_intent")
@patch("src.agent.classify_safety")
def test_F023_process_ticket_returns_required_batch_fields(
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
            "ticket_id": "T-001",
            "customer_name": "김민수",
            "customer_type": "재진",
            "message": "내일 2시 내과 예약",
        },
        all_appointments=[],
        existing_appointment=None,
        session_state=None,
        now=REFERENCE_NOW,
    )

    assert REQUIRED_RESULT_KEYS.issubset(result.keys())
    assert result["ticket_id"] == "T-001"
    assert result["classified_intent"] == "book_appointment"
    assert result["action"] == "book_appointment"
    assert result["department"] == "내과"
    assert isinstance(result["confidence"], float)
    assert result["reasoning"]


@patch("src.agent.classify_safety")
def test_F024_confidence_and_reasoning_reflect_pipeline_evidence(mock_classify_safety):
    mock_classify_safety.return_value = {
        "category": "medical_advice",
        "department_hint": None,
        "mixed_department_guidance": False,
        "unsupported_department": None,
        "unsupported_doctor": None,
    }

    reject_result = process_ticket(
        {
            "ticket_id": "T-REJECT",
            "customer_type": "재진",
            "message": "이 약 먹어도 되나요?",
        },
        all_appointments=[],
        now=REFERENCE_NOW,
    )

    assert reject_result["action"] == "reject"
    assert "의료 상담 요청 감지" in reject_result["reasoning"]
    assert "safety gate에서 reject" in reject_result["reasoning"]

    with patch("src.agent.classify_safety") as safe_mock, patch("src.agent.classify_intent") as intent_mock, patch(
        "src.agent.apply_policy"
    ) as policy_mock:
        safe_mock.return_value = SAFE_RESULT
        intent_mock.return_value = {
            "action": "clarify",
            "department": "내과",
            "date": None,
            "time": None,
            "missing_info": ["date", "time"],
        }
        policy_mock.return_value = {
            "allowed": True,
            "reason": "정책 검사를 통과했습니다.",
            "recommended_action": "book_appointment",
        }

        clarify_result = process_ticket(
            {
                "ticket_id": "T-CLARIFY",
                "customer_type": "재진",
                "message": "내과 예약하고 싶어요",
            },
            all_appointments=[],
            now=REFERENCE_NOW,
        )

    assert clarify_result["action"] == "clarify"
    assert "필수 정보 부족(date, time)" in clarify_result["reasoning"]
    assert reject_result["confidence"] != clarify_result["confidence"]


@patch("run.process_ticket")
def test_F022_run_batch_reads_and_writes_json_via_agent(mock_process_ticket, tmp_path):
    input_path = tmp_path / "tickets.json"
    output_path = tmp_path / "results.json"
    tickets = [
        {
            "ticket_id": "T-001",
            "customer_name": "김민수",
            "customer_type": "재진",
            "message": "내일 예약",
            "timestamp": "2026-03-24T10:00:00+09:00",
            "context": {},
        },
        {
            "ticket_id": "T-002",
            "customer_name": "이서연",
            "customer_type": "초진",
            "message": "예약 취소",
            "timestamp": "2026-03-24T11:00:00+09:00",
            "context": {},
        },
    ]
    input_path.write_text(json.dumps(tickets, ensure_ascii=False), encoding="utf-8")

    mock_process_ticket.side_effect = [
        {
            "ticket_id": "T-001",
            "classified_intent": "book_appointment",
            "department": "내과",
            "action": "book_appointment",
            "response": "예약 진행 여부를 확인합니다.",
            "confidence": 0.91,
            "reasoning": "재진 환자, 내과, 시간 정보 확인, 정책 위반 없음",
        },
        {
            "ticket_id": "T-002",
            "classified_intent": "cancel_appointment",
            "department": None,
            "action": "clarify",
            "response": "어떤 예약을 취소할지 알려주세요.",
            "confidence": 0.67,
            "reasoning": "취소 의도는 분명하지만 대상 예약 식별 정보 부족",
        },
    ]

    results = run.run_batch(str(input_path), str(output_path))
    written = json.loads(output_path.read_text(encoding="utf-8"))

    assert results == written
    assert len(results) == 2
    assert all(REQUIRED_RESULT_KEYS == set(item.keys()) for item in results)
    assert mock_process_ticket.call_count == 2
    first_call_ticket = mock_process_ticket.call_args_list[0].args[0]
    first_call_now = mock_process_ticket.call_args_list[0].kwargs["now"]
    assert first_call_ticket["ticket_id"] == "T-001"
    assert isinstance(first_call_now, datetime)
