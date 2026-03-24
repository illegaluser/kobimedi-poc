
from datetime import datetime, timezone
from .classifier import safety_check as classify_safety, classify_intent
from .response_builder import build_response
from .policy import apply_policy


def _build_safety_response(safety_result: dict) -> dict:
    category = safety_result.get("category")

    if category == "emergency":
        return build_response(
            action="escalate",
            message="급성 통증 또는 응급 가능성이 있어 자동 예약 대신 상담원 또는 의료진 확인이 먼저 필요합니다.",
        )

    if category == "medical_advice":
        return build_response(
            action="reject",
            message="의료법상 의료 상담(진단, 약물, 치료 방법 안내)은 도와드릴 수 없습니다. 원하시면 진료 예약을 도와드릴게요.",
        )

    if category == "off_topic":
        return build_response(
            action="reject",
            message="코비메디 예약 관련 문의만 도와드릴 수 있습니다.",
        )

    return build_response(
        action="reject",
        message="안전성 판단에 실패했습니다. 예약 관련 문의를 다시 작성해 주세요.",
    )


def _build_unknown_entity_response(safety_result: dict) -> dict | None:
    unsupported_department = safety_result.get("unsupported_department")
    unsupported_doctor = safety_result.get("unsupported_doctor")

    if unsupported_department:
        return build_response(
            action="reject",
            message=f"코비메디에는 {unsupported_department}가 없습니다. 현재 예약 가능한 분과는 이비인후과, 내과, 정형외과입니다.",
        )

    if unsupported_doctor:
        return build_response(
            action="reject",
            message=f"{unsupported_doctor}은(는) 코비메디에서 확인되지 않습니다. 현재 확인 가능한 의료진은 이춘영 원장, 김만수 원장, 원징수 원장입니다.",
        )

    return None


def _build_department_guidance_response(department: str | None) -> dict:
    if department:
        return build_response(
            action="clarify",
            message=f"증상만으로 진단이나 치료 방법을 안내할 수는 없지만, 예약 안내 기준으로는 {department} 진료가 적절할 수 있습니다. 원하시는 날짜와 시간을 알려주시면 {department} 예약을 도와드릴게요.",
            department=department,
        )

    return build_response(
        action="clarify",
        message="증상만으로 진단은 도와드릴 수 없습니다. 예약을 원하시면 원하시는 날짜, 시간, 진료과를 알려주세요.",
    )


def _normalize_safety_result(safety_output) -> dict:
    if isinstance(safety_output, dict):
        return safety_output

    category = safety_output or "classification_error"
    return {
        "category": category,
        "department_hint": None,
        "mixed_department_guidance": False,
        "unsupported_department": None,
        "unsupported_doctor": None,
    }

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
    safety_result = _normalize_safety_result(classify_safety(user_message))

    if safety_result.get("category") != "safe":
        return _build_safety_response(safety_result)

    unknown_entity_response = _build_unknown_entity_response(safety_result)
    if unknown_entity_response is not None:
        return unknown_entity_response

    if safety_result.get("mixed_department_guidance"):
        return _build_department_guidance_response(safety_result.get("department_hint"))

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
        # 정책 위반은 clarify가 아니라 reject로 처리
        # (정보 부족이 아니라 정책 거절이므로)
        return build_response(
            action="reject",
            message=policy_result["reason"],
            department=intent_result.get("department")
        )
    
    # If policy passes, build a success response.
    action = intent_result.get("action")
    department = intent_result.get("department") or safety_result.get("department_hint")
    
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
    elif action == "clarify":
        if department:
            response_message = f"{department} 예약을 도와드릴 수 있습니다. 원하시는 날짜와 시간을 알려주시겠어요?"
        else:
            response_message = "예약 관련하여 더 자세한 정보가 필요합니다. 원하시는 날짜, 시간, 진료과를 알려주시겠어요?"

    return build_response(
        action=action,
        message=response_message,
        department=department
    )
