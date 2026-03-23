
from datetime import datetime, timedelta, timezone

# --- Constants ---
POLICY_REASONS = {
    "SLOT_UNAVAILABLE": "요청하신 시간에는 예약이 이미 가득 찼습니다. 다른 시간을 선택해 주세요.",
    "SLOT_FULL_CAPACITY": "해당 시간대에는 예약 인원이 가득 찼습니다. 다른 시간대를 선택해 주세요.",
    "CHANGE_WINDOW_EXPIRED": "예약 변경 및 취소는 예약 시간 기준 24시간 전까지만 가능합니다.",
    "NO_EXISTING_APPOINTMENT": "확인, 변경 또는 취소할 기존 예약 정보를 찾을 수 없습니다.",
    "SUCCESS": "정책 검사를 통과했습니다."
}

APPOINTMENT_DURATION_MINS = {
    "초진": 40,
    "재진": 30,
}
MAX_APPOINTMENTS_PER_HOUR = 3

# --- Helper Functions ---

def _parse_time(time_str: str) -> datetime:
    """Parses an ISO 8601 formatted string into a timezone-aware datetime object."""
    return datetime.fromisoformat(time_str).astimezone(timezone.utc)

# --- Policy Check Functions ---

def is_change_allowed(appointment_time_str: str, now: datetime) -> bool:
    """
    Checks if a change or cancellation is allowed based on the 24-hour rule.
    (F-010)
    """
    appointment_time = _parse_time(appointment_time_str)
    # Allows changes if the request is made exactly 24 hours before or earlier.
    return now <= (appointment_time - timedelta(hours=24))

def is_slot_available(
    requested_start_str: str,
    customer_type: str,
    existing_appointments: list[dict]
) -> tuple[bool, str]:
    """
    Checks if a new appointment can be booked.
    - F-009: Checks for max 3 appointments in the same hour window.
    - F-011: Checks for time slot overlaps based on customer type (30/40 mins).
    """
    requested_start = _parse_time(requested_start_str)
    duration_mins = APPOINTMENT_DURATION_MINS.get(customer_type, 30)
    requested_end = requested_start + timedelta(minutes=duration_mins)

    # F-009: Check max capacity per hour
    # The window is the hour of the requested start time (e.g., 2:30pm is in the 2:00pm-3:00pm window)
    window_start = requested_start.replace(minute=0, second=0, microsecond=0)
    window_end = window_start + timedelta(hours=1)
    
    appointments_in_window = 0
    for appt in existing_appointments:
        appt_start = _parse_time(appt["booking_time"])
        if window_start <= appt_start < window_end:
            appointments_in_window += 1
    
    if appointments_in_window >= MAX_APPOINTMENTS_PER_HOUR:
        return False, POLICY_REASONS["SLOT_FULL_CAPACITY"]

    # F-011: Check for direct time overlap
    for appt in existing_appointments:
        appt_start = _parse_time(appt["booking_time"])
        appt_duration = APPOINTMENT_DURATION_MINS.get(appt["customer_type"], 30)
        appt_end = appt_start + timedelta(minutes=appt_duration)
        
        # Check for overlap: (StartA < EndB) and (EndA > StartB)
        if requested_start < appt_end and requested_end > appt_start:
            return False, POLICY_REASONS["SLOT_UNAVAILABLE"]
            
    return True, POLICY_REASONS["SUCCESS"]

# --- Main Policy Application Function ---

def apply_policy(intent: dict, existing_appointment: dict, all_appointments: list[dict], now: datetime) -> dict:
    """
    Applies the hospital's policies to a classified user intent.

    Args:
        intent: The classified intent from the classifier, including 'action',
                'department', 'booking_time', 'customer_type'.
        existing_appointment: The user's existing appointment, if found.
        all_appointments: A list of all appointments in the system.
        now: The current time, for time-sensitive checks.

    Returns:
        A dictionary with 'allowed' (bool) and 'reason' (str).
    """
    action = intent.get("action")

    if action == "book_appointment":
        allowed, reason = is_slot_available(
            requested_start_str=intent.get("booking_time"),
            customer_type=intent.get("customer_type", "재진"),
            existing_appointments=all_appointments
        )
        return {"allowed": allowed, "reason": reason}

    elif action in ["modify_appointment", "cancel_appointment"]:
        # F-012: Check if there is an appointment to modify/cancel
        if not existing_appointment:
            return {"allowed": False, "reason": POLICY_REASONS["NO_EXISTING_APPOINTMENT"]}
        
        # F-010: Check if the change is within the allowed time window
        if not is_change_allowed(existing_appointment["booking_time"], now):
            return {"allowed": False, "reason": POLICY_REASONS["CHANGE_WINDOW_EXPIRED"]}
        
        # For modification, we also need to check if the new slot is available
        if action == "modify_appointment":
            # Remove the original appointment from the list to avoid self-collision
            other_appointments = [
                appt for appt in all_appointments 
                if appt["id"] != existing_appointment["id"]
            ]
            allowed, reason = is_slot_available(
                requested_start_str=intent.get("booking_time"),
                customer_type=existing_appointment.get("customer_type", "재진"),
                existing_appointments=other_appointments
            )
            return {"allowed": allowed, "reason": reason}

        return {"allowed": True, "reason": POLICY_REASONS["SUCCESS"]}

    elif action == "check_appointment":
        # F-012: Check if there is an appointment to check
        if not existing_appointment:
            return {"allowed": False, "reason": POLICY_REASONS["NO_EXISTING_APPOINTMENT"]}
        return {"allowed": True, "reason": POLICY_REASONS["SUCCESS"]}
        
    # For actions like 'clarify', no policy check is needed
    return {"allowed": True, "reason": POLICY_REASONS["SUCCESS"]}

