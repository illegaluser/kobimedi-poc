
import json
import ollama
from .prompts import SAFETY_GATE_PROMPT_TEMPLATE, INTENT_CLASSIFICATION_PROMPT_TEMPLATE

def classify_safety(user_message: str) -> str:
    """
    Classifies a user message into safety categories using an LLM.

    Args:
        user_message: The user's input message.

    Returns:
        A string representing the classified category: "safe", "emergency",
        "medical_advice", "off_topic", or "classification_error" on failure.
    """
    prompt = SAFETY_GATE_PROMPT_TEMPLATE.format(user_message=user_message)
    
    try:
        response = ollama.chat(
            model='qwen3-coder:30b',
            messages=[{'role': 'user', 'content': prompt}],
            format='json'
        )
        
        content = response['message']['content']
        result = json.loads(content)
        
        category = result.get("category")
        
        if category in ["safe", "emergency", "medical_advice", "off_topic"]:
            return category
        else:
            # The model returned a value not in the expected set
            return "classification_error"
            
    except (json.JSONDecodeError, KeyError, TypeError, Exception) as e:
        # Handle cases where the response is not valid JSON, doesn't have the key,
        # or any other unexpected error during the Ollama call.
        print(f"Error during safety classification: {e}")
        return "classification_error"

def classify_intent(user_message: str) -> dict:
    """
    Classifies a user message to determine intent (action and department).

    Args:
        user_message: The user's input message.

    Returns:
        A dictionary containing "action" and "department".
        On failure, defaults to a clarification action.
    """
    prompt = INTENT_CLASSIFICATION_PROMPT_TEMPLATE.format(user_message=user_message)
    
    try:
        response = ollama.chat(
            model='qwen3-coder:30b',
            messages=[{'role': 'user', 'content': prompt}],
            format='json'
        )
        
        content = response['message']['content']
        result = json.loads(content)
        
        action = result.get("action")
        department = result.get("department")
        
        # Basic validation
        valid_actions = [
            "book_appointment", "modify_appointment", "cancel_appointment",
            "check_appointment", "clarify"
            # escalate/reject는 safety gate에서 처리하므로 LLM 반환값 유효성 검증에서 제외
            # 만약 LLM이 반환해도 아래 else 분기에서 clarify로 안전하게 처리됨
        ]
        valid_departments = ["이비인후과", "내과", "정형외과", None]

        if action in valid_actions and department in valid_departments:
            return {"action": action, "department": department}
        else:
            # The model returned values not in the expected set
            return {"action": "clarify", "department": None, "error": True}

    except (json.JSONDecodeError, KeyError, TypeError, Exception) as e:
        print(f"Error during intent classification: {e}")
        return {"action": "clarify", "department": None, "error": True}
