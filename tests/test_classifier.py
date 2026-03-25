
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
                "doctor_name": None,
                "date": "2026-03-25",
                "time": "14:00",
                "customer_type": None,
                "is_first_visit": False,
                "missing_info": [],
                "target_appointment_hint": None,
            },
            {
                "action": "clarify",
                "department": "이비인후과",
                "doctor_name": None,
                "date": "2026-03-25",
                "time": "14:00",
                "customer_type": None,
                "missing_info": ["customer_type"],
                "target_appointment_hint": None,
            },
        ),
        (
            "내일 오후 2시 이비인후과 초진 예약",
            {
                "action": "book_appointment",
                "department": "이비인후과",
                "doctor_name": None,
                "date": "2026-03-25",
                "time": "14:00",
                "customer_type": "초진",
                "is_first_visit": True,
                "missing_info": [],
                "target_appointment_hint": None,
            },
            {
                "action": "book_appointment",
                "department": "이비인후과",
                "doctor_name": None,
                "date": "2026-03-25",
                "time": "14:00",
                "customer_type": "초진",
                "missing_info": [],
                "target_appointment_hint": None,
            },
        ),
        (
            "수요일 예약을 목요일 오후 3시로 변경해주세요",
            {
                "action": "modify_appointment",
                "department": None,
                "doctor_name": None,
                "date": "2026-03-26",
                "time": "15:00",
                "customer_type": None,
                "is_first_visit": None,
                "missing_info": [],
                "target_appointment_hint": {
                    "appointment_id": None,
                    "department": None,
                    "doctor_name": None,
                    "date": "2026-03-25",
                    "time": None,
                    "booking_time": None,
                },
            },
            {
                "action": "modify_appointment",
                "department": None,
                "doctor_name": None,
                "date": "2026-03-26",
                "time": "15:00",
                "customer_type": None,
                "missing_info": [],
                "target_appointment_hint": {"date": "2026-03-25"},
            },
        ),
        (
            "금요일 오후 4시 내과 예약 취소",
            {
                "action": "cancel_appointment",
                "department": "내과",
                "doctor_name": None,
                "date": "2026-03-27",
                "time": "16:00",
                "customer_type": None,
                "is_first_visit": None,
                "missing_info": [],
                "target_appointment_hint": {
                    "appointment_id": None,
                    "department": "내과",
                    "doctor_name": None,
                    "date": "2026-03-27",
                    "time": "16:00",
                    "booking_time": None,
                },
            },
            {
                "action": "cancel_appointment",
                "department": "내과",
                "doctor_name": None,
                "date": "2026-03-27",
                "time": "16:00",
                "customer_type": None,
                "missing_info": [],
                "target_appointment_hint": {
                    "department": "내과",
                    "date": "2026-03-27",
                    "time": "16:00",
                },
            },
        ),
        (
            "다음 주 예약 확인",
            {
                "action": "check_appointment",
                "department": None,
                "doctor_name": None,
                "date": None,
                "time": None,
                "customer_type": None,
                "is_first_visit": None,
                "missing_info": [],
                "target_appointment_hint": None,
            },
            {
                "action": "clarify",
                "department": None,
                "doctor_name": None,
                "date": None,
                "time": None,
                "customer_type": None,
                "missing_info": ["appointment_target"],
                "target_appointment_hint": None,
            },
        ),
        (
            "예약하고 싶어요",
            {
                "action": "book_appointment",
                "department": None,
                "doctor_name": None,
                "date": None,
                "time": None,
                "customer_type": None,
                "is_first_visit": None,
                "missing_info": [],
                "target_appointment_hint": None,
            },
            {
                "action": "clarify",
                "department": None,
                "doctor_name": None,
                "date": None,
                "time": None,
                "customer_type": None,
                "missing_info": ["department", "date", "time", "customer_type"],
                "target_appointment_hint": None,
            },
        ),
        (
            "콧물이 나요. 어디로 가야 하나요?",
            {
                "action": "clarify",
                "department": "이비인후과",
                "doctor_name": None,
                "date": None,
                "time": None,
                "customer_type": None,
                "is_first_visit": None,
                "missing_info": ["date", "time"],
                "target_appointment_hint": None,
            },
            {
                "action": "clarify",
                "department": "이비인후과",
                "doctor_name": None,
                "date": None,
                "time": None,
                "customer_type": None,
                "missing_info": ["date", "time"],
                "target_appointment_hint": None,
            },
        ),
        (
            "이춘영 원장님 예약",
            {
                "action": "book_appointment",
                "department": None,
                "doctor_name": "이춘영 원장",
                "date": None,
                "time": None,
                "customer_type": None,
                "is_first_visit": None,
                "missing_info": [],
                "target_appointment_hint": None,
            },
            {
                "action": "clarify",
                "department": "이비인후과",
                "doctor_name": "이춘영 원장",
                "date": None,
                "time": None,
                "customer_type": None,
                "missing_info": ["date", "time", "customer_type"],
                "target_appointment_hint": None,
            },
        ),
        (
            "다음주 수요일 오후 2시에 내과 재진 예약",
            {
                "action": "book_appointment",
                "department": "내과",
                "doctor_name": None,
                "date": "2026-04-01",
                "time": "14:00",
                "customer_type": "재진",
                "is_first_visit": False,
                "missing_info": [],
                "target_appointment_hint": None,
            },
            {
                "action": "book_appointment",
                "department": "내과",
                "doctor_name": None,
                "date": "2026-04-01",
                "time": "14:00",
                "customer_type": "재진",
                "missing_info": [],
                "target_appointment_hint": None,
            },
        ),
        (
            "모레 정형외과",
            {
                "action": "book_appointment",
                "department": "정형외과",
                "doctor_name": None,
                "date": "2026-03-26",
                "time": None,
                "customer_type": None,
                "is_first_visit": None,
                "missing_info": ["time", "customer_type"],
                "target_appointment_hint": None,
            },
            {
                "action": "clarify",
                "department": "정형외과",
                "doctor_name": None,
                "date": "2026-03-26",
                "time": None,
                "customer_type": None,
                "missing_info": ["time", "customer_type"],
                "target_appointment_hint": None,
            },
        ),
    ],
)
@patch("src.classifier.ollama.chat")
def test_classify_intent_required_scenarios(mock_ollama_chat, user_message, llm_payload, expected):
    mock_ollama_chat.return_value = _mock_ollama_payload(llm_payload)

    result = classify_intent(user_message, now=REFERENCE_NOW)

    assert result["action"] == expected["action"]
    assert result["department"] == expected["department"]
    assert result["doctor_name"] == expected["doctor_name"]
    assert result["customer_type"] == expected["customer_type"]
    assert result["missing_info"] == expected["missing_info"]
    # F-014: classified_intent must be present and must be a valid action enum value
    assert "classified_intent" in result
    assert result["classified_intent"] in [
        "book_appointment", "modify_appointment", "cancel_appointment",
        "check_appointment", "clarify", "escalate", "reject",
    ]

    if "date" in expected:
        assert result["date"] == expected["date"]
    if "time" in expected:
        assert result["time"] == expected["time"]

    expected_target_hint = expected["target_appointment_hint"]
    if expected_target_hint is None:
        assert result["target_appointment_hint"] is None
    else:
        assert result["target_appointment_hint"] is not None
        for key, value in expected_target_hint.items():
            assert result["target_appointment_hint"][key] == value

    assert "감기입니다" not in json.dumps(result, ensure_ascii=False)
    mock_ollama_chat.assert_called_once()


@patch("src.classifier.ollama.chat")
def test_classify_intent_classified_intent_preserved_when_action_overridden_to_clarify(mock_ollama_chat):
    """F-014: classified_intent reflects user intent; action may differ (e.g. clarify due to missing info)."""
    mock_ollama_chat.return_value = _mock_ollama_payload(
        {
            "action": "book_appointment",
            "department": "이비인후과",
            "doctor_name": None,
            "date": "2026-03-25",
            "time": "14:00",
            "customer_type": None,
            "is_first_visit": None,
            "missing_info": [],
            "target_appointment_hint": None,
        }
    )

    result = classify_intent("내일 오후 2시 이비인후과 예약", now=REFERENCE_NOW)

    # Action is overridden to clarify due to missing customer_type
    assert result["action"] == "clarify"
    # But classified_intent captures the original user intent
    assert result["classified_intent"] == "book_appointment"


@patch("src.classifier.ollama.chat")
def test_classify_intent_invalid_enum_uses_rule_action(mock_ollama_chat):
    mock_ollama_chat.return_value = _mock_ollama_payload(
        {
            "action": "make_booking",
            "department": "내과",
            "doctor_name": None,
            "date": "2026-03-25",
            "time": "14:00",
            "customer_type": "재진",
            "is_first_visit": False,
            "missing_info": [],
            "target_appointment_hint": None,
        }
    )

    result = classify_intent("내일 오후 2시 내과 재진 예약", now=REFERENCE_NOW)

    assert result["action"] == "book_appointment"
    assert result["department"] == "내과"
    assert result["customer_type"] == "재진"


@patch("src.classifier.ollama.chat")
def test_classify_intent_json_decode_error_returns_safe_clarify(mock_ollama_chat):
    mock_ollama_chat.return_value = {"message": {"content": "this is not json"}}

    result = classify_intent("아무 말", now=REFERENCE_NOW)

    assert result["action"] == "clarify"
    assert result["department"] is None
    assert result["missing_info"] == []
    assert result["error"] is True


@patch("src.classifier.ollama.chat")
def test_classify_intent_json_decode_error_retries_once(mock_ollama_chat):
    mock_ollama_chat.side_effect = [
        {"message": {"content": "this is not json"}},
        _mock_ollama_payload(
            {
                "action": "book_appointment",
                "department": "내과",
                "doctor_name": None,
                "date": "2026-03-25",
                "time": "14:00",
                "customer_type": "재진",
                "is_first_visit": False,
                "missing_info": [],
                "target_appointment_hint": None,
            }
        ),
    ]

    result = classify_intent("내일 오후 2시 내과 재진 예약", now=REFERENCE_NOW)

    assert result["action"] == "book_appointment"
    assert result["department"] == "내과"
    assert result["customer_type"] == "재진"
    assert result["date"] == "2026-03-25"
    assert result["time"] == "14:00"
    assert mock_ollama_chat.call_count == 2


@patch("src.classifier.ollama.chat", side_effect=Exception("Ollama connection failed"))
def test_classify_intent_ollama_failure_returns_safe_clarify(mock_ollama_chat):
    result = classify_intent("아무 말", now=REFERENCE_NOW)

    assert result["action"] == "clarify"
    assert result["department"] is None
    assert result["missing_info"] == []
    assert result["error"] is True
    assert result["fallback_action"] == "clarify"
    assert "일시적 오류" in result["fallback_message"]
    mock_ollama_chat.assert_called_once()


@patch("src.classifier.ollama.chat", side_effect=TimeoutError("timed out"))
def test_classify_intent_timeout_returns_safe_clarify(mock_ollama_chat):
    result = classify_intent("아무 말", now=REFERENCE_NOW)

    assert result["action"] == "clarify"
    assert result["department"] is None
    assert result["missing_info"] == []
    assert result["error"] is True
    assert result["fallback_action"] == "clarify"
    assert "일시적 오류" in result["fallback_message"]
    mock_ollama_chat.assert_called_once()


@patch("src.classifier.ollama.chat")
def test_classify_intent_prefers_doctor_department_mapping(mock_ollama_chat):
    mock_ollama_chat.return_value = _mock_ollama_payload(
        {
            "action": "book_appointment",
            "department": None,
            "doctor_name": None,
            "date": None,
            "time": None,
            "customer_type": None,
            "is_first_visit": None,
            "missing_info": [],
            "target_appointment_hint": None,
        }
    )

    result = classify_intent("김만수 원장님 예약", now=REFERENCE_NOW)

    assert result["department"] == "내과"
    assert result["doctor_name"] == "김만수 원장"
    assert result["action"] == "clarify"
    assert result["missing_info"] == ["date", "time", "customer_type"]


@patch("src.classifier.ollama.chat")
def test_classify_intent_detects_proxy_booking_and_patient_entities(mock_ollama_chat):
    mock_ollama_chat.return_value = _mock_ollama_payload(
        {
            "action": "book_appointment",
            "department": "내과",
            "doctor_name": None,
            "date": "2026-03-25",
            "time": "14:00",
            "customer_type": "초진",
            "is_first_visit": True,
            "patient_name": "김영희",
            "patient_contact": "01012345678",
            "birth_date": "1955-05-05",
            "is_proxy_booking": True,
            "symptom_keywords": [],
            "missing_info": [],
            "target_appointment_hint": None,
        }
    )

    result = classify_intent(
        "엄마를 대신해서 환자 이름은 김영희, 연락처는 010-1234-5678이고 생년월일은 1955-05-05입니다. 내일 오후 2시 내과 초진 예약",
        now=REFERENCE_NOW,
    )

    assert result["action"] == "book_appointment"
    assert result["department"] == "내과"
    assert result["customer_type"] == "초진"
    assert result["patient_name"] == "김영희"
    assert result["patient_contact"] == "010-1234-5678"
    assert result["birth_date"] == "1955-05-05"
    assert result["is_proxy_booking"] is True


@patch("src.classifier.ollama.chat")
def test_classify_intent_maps_symptom_to_department_without_diagnosis(mock_ollama_chat):
    mock_ollama_chat.return_value = _mock_ollama_payload(
        {
            "action": "book_appointment",
            "department": "이비인후과",
            "doctor_name": None,
            "date": "2026-03-25",
            "time": "10:00",
            "customer_type": None,
            "is_first_visit": None,
            "patient_name": None,
            "patient_contact": None,
            "birth_date": None,
            "is_proxy_booking": False,
            "symptom_keywords": ["코막힘"],
            "missing_info": [],
            "target_appointment_hint": None,
        }
    )

    result = classify_intent("코막힘이 심해서 내일 오전 10시에 예약하고 싶어요", now=REFERENCE_NOW)

    assert result["action"] == "clarify"
    assert result["department"] == "이비인후과"
    assert "코막힘" in result["symptom_keywords"]
    assert result["missing_info"] == ["customer_type"]
    assert "감기" not in json.dumps(result, ensure_ascii=False)


@patch("src.classifier.ollama.chat", side_effect=ConnectionRefusedError("connection refused"))
def test_classify_intent_connection_refused_returns_safe_clarify(mock_ollama_chat):
    result = classify_intent("예약 도와주세요", now=REFERENCE_NOW)

    assert result["action"] == "clarify"
    assert result["error"] is True
    assert result["fallback_action"] == "clarify"
    assert "일시적 오류" in result["fallback_message"]
    mock_ollama_chat.assert_called_once()

