
import pytest
import json
from unittest.mock import patch, MagicMock
from src.classifier import classify_intent

# Test cases for intent classification
# Format: (user_message, mock_ollama_response, expected_action, expected_department)
INTENT_TEST_CASES = [
    # F-005: Basic actions
    ("내일 오후 2시 예약하고 싶어요", {'action': 'book_appointment', 'department': None}, 'book_appointment', None),
    ("3시 예약을 4시로 바꿔주세요", {'action': 'modify_appointment', 'department': None}, 'modify_appointment', None),
    ("예약 취소할게요", {'action': 'cancel_appointment', 'department': None}, 'cancel_appointment', None),
    ("제 예약 좀 확인해주세요", {'action': 'check_appointment', 'department': None}, 'check_appointment', None),

    # F-006: Department specified
    ("내일 내과 예약 가능한가요?", {'action': 'book_appointment', 'department': '내과'}, 'book_appointment', '내과'),
    ("정형외과 예약 변경하고 싶어요", {'action': 'modify_appointment', 'department': '정형외과'}, 'modify_appointment', '정형외과'),

    # F-007: Department inferred from symptoms
    ("계속 콧물이 나는데 진료볼 수 있을까요?", {'action': 'book_appointment', 'department': '이비인후과'}, 'book_appointment', '이비인후과'),
    ("속이 쓰려서요. 오늘 진료 되나요?", {'action': 'book_appointment', 'department': '내과'}, 'book_appointment', '내과'),

    # F-008: Clarify needed
    ("예약하고 싶어요.", {'action': 'clarify', 'department': None}, 'clarify', None),
    ("도와주세요", {'action': 'clarify', 'department': None}, 'clarify', None),
]

@pytest.mark.parametrize(
    "user_message, mock_ollama_response, expected_action, expected_department",
    INTENT_TEST_CASES
)
@patch('src.classifier.ollama.chat')
def test_classify_intent_scenarios(mock_ollama_chat, user_message, mock_ollama_response, expected_action, expected_department):
    """
    Tests the classify_intent function with various scenarios, mocking the Ollama response.
    """
    # Arrange
    # The mock response should be a dictionary that ollama.chat would return
    mock_response_content = json.dumps(mock_ollama_response)
    mock_ollama_chat.return_value = {'message': {'content': mock_response_content}}

    # Act
    result = classify_intent(user_message)

    # Assert
    assert result['action'] == expected_action
    assert result['department'] == expected_department
    mock_ollama_chat.assert_called_once()

# Test cases for error handling
@patch('src.classifier.ollama.chat')
def test_classify_intent_json_decode_error(mock_ollama_chat):
    """
    Tests that classify_intent handles JSON decoding errors gracefully.
    """
    # Arrange
    mock_ollama_chat.return_value = {'message': {'content': 'this is not json'}}

    # Act
    result = classify_intent("any message")

    # Assert
    assert result['action'] == 'clarify'
    assert result['department'] is None
    assert result['error'] is True

@patch('src.classifier.ollama.chat')
def test_classify_intent_key_error(mock_ollama_chat):
    """
    Tests that classify_intent handles missing keys in the JSON response.
    """
    # Arrange
    mock_response_content = json.dumps({"wrong_key": "book_appointment"})
    mock_ollama_chat.return_value = {'message': {'content': mock_response_content}}

    # Act
    result = classify_intent("any message")

    # Assert
    assert result['action'] == 'clarify'
    assert result['department'] is None
    assert result['error'] is True

@patch('src.classifier.ollama.chat', side_effect=Exception("Ollama connection failed"))
def test_classify_intent_ollama_exception(mock_ollama_chat):
    """
    Tests that classify_intent handles exceptions from the ollama call itself.
    """
    # Act
    result = classify_intent("any message")

    # Assert
    assert result['action'] == 'clarify'
    assert result['department'] is None
    assert result['error'] is True

