


import pytest

from unittest.mock import patch

from src.agent import process_ticket



# Test cases focus on the agent's routing based on the safety gate output.

TEST_CASES = [

    # Non-safe scenarios

    ("이 약 먹어도 되나요?", "medical_advice", "reject"),

    ("두통이 심한데 무슨 병인가요?", "medical_advice", "reject"),

    ("오늘 날씨 어때?", "off_topic", "reject"),

    ("지금 너무 아픈데 오늘 바로 봐줄 수 있나요?", "emergency", "escalate"),

    ("... (gibberish)", "classification_error", "reject"),



    # Safe scenarios

    ("예약하려는데, 이 증상이면 어느 과가 맞나요?", "safe", "book_appointment"),

    ("내일 오후 2시 이비인후과 예약하고 싶어요.", "safe", "book_appointment"),

]



@pytest.mark.parametrize("user_message, mock_safety_category, expected_final_action", TEST_CASES)

@patch('src.agent.apply_policy')

@patch('src.agent.classify_intent')

@patch('src.agent.classify_safety')

def test_agent_control_flow(mock_classify_safety, mock_classify_intent, mock_apply_policy, user_message, mock_safety_category, expected_final_action):

    """

    Tests the agent's control flow through the safety and intent classification stages.

    The policy stage is mocked to isolate the test to the first two stages.

    """

    # Arrange

    ticket = {"message": user_message, "booking_time": "2026-01-01T12:00:00Z"}

    mock_classify_safety.return_value = mock_safety_category



    # Mock the subsequent stages that are not the focus of this test

    mock_apply_policy.return_value = {"allowed": True} # Assume policy always passes



    if mock_safety_category == 'safe':

        mock_classify_intent.return_value = {

            'action': expected_final_action,

            'department': '이비인후과' # A sample department

        }



    # Act

    # Pass empty lists/None for appointment data as it's not relevant to this test

    result = process_ticket(ticket, all_appointments=[], existing_appointment=None)



    # Assert

    mock_classify_safety.assert_called_once_with(user_message)



    if mock_safety_category == 'safe':

        mock_classify_intent.assert_called_once_with(user_message)

        mock_apply_policy.assert_called_once()

        assert result["action"] == expected_final_action

    else:

        mock_classify_intent.assert_not_called()

        mock_apply_policy.assert_not_called()

        assert result["action"] == expected_final_action



    assert "response" in result and result["response"]



def test_empty_message():

    """

    Tests that a ticket with an empty or missing message is rejected.

    """

    ticket_none = {"message": None}

    ticket_empty = {"message": ""}



    # Pass empty lists/None for appointment data

    result_none = process_ticket(ticket_none, all_appointments=[], existing_appointment=None)

    result_empty = process_ticket(ticket_empty, all_appointments=[], existing_appointment=None)



    assert result_none["action"] == "reject"

    assert "문의 내용이 없습니다." in result_none["response"]

    

    assert result_empty["action"] == "reject"

    assert "문의 내용이 없습니다." in result_empty["response"]


