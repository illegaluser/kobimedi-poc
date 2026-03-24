
import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from src.classifier import classify_intent


REFERENCE_NOW = datetime(2026, 3, 24, 2, 0, tzinfo=timezone.utc)


def _mock_ollama_payload(payload: dict | None = None) -> dict:
    return {"message": {"content": json.dumps(payload or {})}}


@pytest.mark.parametrize(
    "user_message, llm_payload, expected",
    [
        (
            "내일 오후 2시 이비인후과 예약",
            {
                "action": "book_appointment",
                "department": "이비인후과",
                "date": "2026-03-25",
                "time": "14:00",
                "is_first_visit": False,
                "missing_info": [],
            },
            {
                "action": "book_appointment",
                "department": "이비인후과",
                "date": "2026-03-25",
                "time": "14:00",
                "missing_info": [],
            },
        ),
        (
            "수요일 예약을 목요일로 변경해주세요",
            {
                "action": "modify_appointment",
                "department": None,
                "date": None,
                "time": None,
                "is_first_visit": False,
                "missing_info": [],
            },
            {
                "action": "modify_appointment",
                "department": None,
                "missing_info": [],
            },
        ),
        (
            "금요일 예약 취소",
            {
                "action": "cancel_appointment",
                "department": None,
                "date": None,
                "time": None,
                "is_first_visit": False,
                "missing_info": [],
            },
            {
                "action": "cancel_appointment",
                "department": None,
                "missing_info": [],
            },
        ),
        (
            "다음 주 예약 확인",
            {
                "action": "check_appointment",
                "department": None,
                "date": None,
                "time": None,
                "is_first_visit": False,
                "missing_info": [],
            },
            {
                "action": "check_appointment",
                "department": None,
                "missing_info": [],
            },
        ),
        (
            "예약하고 싶어요",
            {
                "action": "book_appointment",
                "department": None,
                "date": None,
                "time": None,
                "is_first_visit": False,
                "missing_info": [],
            },
            {
                "action": "clarify",
                "department": None,
                "missing_info": ["department", "date", "time"],
            },
        ),
        (
            "도와주세요",
            {
                "action": "clarify",
                "department": None,
                "date": None,
                "time": None,
                "is_first_visit": False,
                "missing_info": [],
            },
            {
                "action": "clarify",
                "department": None,
                "missing_info": [],
            },
        ),
        (
            "콧물이 나요. 어디로 가야 하나요?",
            {
                "action": "clarify",
                "department": "이비인후과",
                "date": None,
                "time": None,
                "is_first_visit": False,
                "missing_info": ["date", "time"],
            },
            {
                "action": "clarify",
                "department": "이비인후과",
                "missing_info": ["date", "time"],
            },
        ),
        (
            "이춘영 원장님 예약",
            {
                "action": "book_appointment",
                "department": None,
                "date": None,
                "time": None,
                "is_first_visit": False,
                "missing_info": [],
            },
            {
                "action": "clarify",
                "department": "이비인후과",
                "missing_info": ["date", "time"],
            },
        ),
        (
            "다음주 수요일 오후 2시에 내과 예약",
            {
                "action": "book_appointment",
                "department": "내과",
                "date": "2026-04-01",
                "time": "14:00",
                "is_first_visit": False,
                "missing_info": [],
            },
            {
                "action": "book_appointment",
                "department": "내과",
                "date": "2026-04-01",
                "time": "14:00",
                "missing_info": [],
            },
        ),
        (
            "모레 정형외과",
            {
                "action": "book_appointment",
                "department": "정형외과",
                "date": "2026-03-26",
                "time": None,
                "is_first_visit": False,
                "missing_info": ["time"],
            },
            {
                "action": "clarify",
                "department": "정형외과",
                "date": "2026-03-26",
                "time": None,
                "missing_info": ["time"],
            },
        ),
    ],
)
@patch("src.classifier.ollama.chat")
def test_classify_intent_required_scenarios(
    mock_ollama_chat,
    user_message,
    llm_payload,
    expected,
):
    mock_ollama_chat.return_value = _mock_ollama_payload(llm_payload)

    result = classify_intent(user_message, now=REFERENCE_NOW)

    assert result["action"] == expected["action"]
    assert result["department"] == expected["department"]
    assert result["missing_info"] == expected["missing_info"]

    if "date" in expected:
        assert result["date"] == expected["date"]
    if "time" in expected:
        assert result["time"] == expected["time"]

    assert "감기입니다" not in json.dumps(result, ensure_ascii=False)
    mock_ollama_chat.assert_called_once()


@patch("src.classifier.ollama.chat")
def test_classify_intent_json_decode_error_returns_safe_clarify(mock_ollama_chat):
    mock_ollama_chat.return_value = {"message": {"content": "this is not json"}}

    result = classify_intent("아무 말", now=REFERENCE_NOW)

    assert result["action"] == "clarify"
    assert result["department"] is None
    assert result["missing_info"] == []
    assert result["error"] is True


@patch("src.classifier.ollama.chat", side_effect=Exception("Ollama connection failed"))
def test_classify_intent_ollama_failure_returns_safe_clarify(mock_ollama_chat):
    result = classify_intent("아무 말", now=REFERENCE_NOW)

    assert result["action"] == "clarify"
    assert result["department"] is None
    assert result["missing_info"] == []
    assert result["error"] is True
    mock_ollama_chat.assert_called_once()


@patch("src.classifier.ollama.chat")
def test_classify_intent_prefers_doctor_department_mapping(mock_ollama_chat):
    mock_ollama_chat.return_value = _mock_ollama_payload(
        {
            "action": "book_appointment",
            "department": None,
            "date": None,
            "time": None,
            "is_first_visit": False,
            "missing_info": [],
        }
    )

    result = classify_intent("김만수 원장님 예약", now=REFERENCE_NOW)

    assert result["department"] == "내과"
    assert result["action"] == "clarify"
    assert result["missing_info"] == ["date", "time"]

