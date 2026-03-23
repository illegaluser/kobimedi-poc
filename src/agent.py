
from datetime import datetime, timezone
from .classifier import classify_safety, classify_intent
from .response_builder import build_response
from .policy import apply_policy

def process_ticket(
    ticket: dict, 
    all_appointments: list = None, 
    existing_appointment: dict = None, 
    now: datetime = None
) -> dict:
    """
    Processes a single user ticket, from safety classification to action determination.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if all_appointments is None:
        all_appointments = []

    user_message = ticket.get("message")
    if not user_message:
        return build_response(action="reject", message="문의 내용이 없습니다.")

    # 1. Safety Gate
    safety_category = classify_safety(user_message)

    if safety_category != "safe":
        action_map = {
            "emergency": "escalate",
            "medical_advice": "reject",
            "off_topic": "reject",
            "classification_error": "reject",
        }
        message_map = {
            "emergency": "긴급 상황으로 확인되어 상담원에게 전달하겠습니다.",
            "medical_advice": "의료법상 진단, 약물, 치료에 대한 조언은 드릴 수 없습니다. 예약 관련 문의만 가능합니다.",
            "off_topic": "의료 예약과 관련 없는 문의는 답변해 드릴 수 없습니다.",
            "classification_error": "문의를 이해하지 못했습니다. 예약 관련 문의를 다시 시도해 주세요.",
        }
        action = action_map.get(safety_category, "reject")
        message = message_map.get(safety_category, "알 수 없는 오류가 발생했습니다.")
        return build_response(action=action, message=message)

    # 2. Intent Classification
    intent_result = classify_intent(user_message)
    if intent_result.get("error"):
        return build_response(
            action="clarify",
            message="의도를 파악하는 중 오류가 발생했습니다. 다시 시도해 주세요."
        )

    # Add other ticket data to the intent for the policy check
    # In a real system, this would be extracted by the LLM or from structured data
    intent_result['booking_time'] = ticket.get('booking_time') # e.g., "2026-04-10T14:15:00Z"
    intent_result['customer_type'] = ticket.get('customer_type', '재진')

    # 3. Policy Application
    policy_result = apply_policy(intent_result, existing_appointment, all_appointments, now)

    if not policy_result["allowed"]:
        # If policy fails, return a clarifying response with the reason.
        return build_response(
            action="clarify",
            message=policy_result["reason"],
            department=intent_result.get("department")
        )
    
    # If policy passes, build a success response.
    action = intent_result.get("action")
    department = intent_result.get("department")
    
    response_message = f"요청하신 '{action}' 작업이 성공적으로 처리되었습니다."
    if action == "book_appointment":
        response_message = f"{department} 예약이 완료되었습니다."
    elif action == "modify_appointment":
        response_message = "예약이 성공적으로 변경되었습니다."
    elif action == "cancel_appointment":
        response_message = "예약이 성공적으로 취소되었습니다."
    elif action == "check_appointment":
        # In a real system, we would format the appointment details here.
        response_message = f"예약이 확인되었습니다: {existing_appointment}"

    return build_response(
        action=action,
        message=response_message,
        department=department
    )
