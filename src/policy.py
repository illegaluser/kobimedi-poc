from __future__ import annotations
from datetime import datetime, timedelta, time

from src.models import Ticket, Booking, PolicyResult, Action


def apply_policy(ticket: Ticket, bookings: list[Booking], now: datetime) -> PolicyResult:
    """
    Applies business logic to a ticket based on deterministic policies.
    """
    intent = ticket.intent

    if intent == "book_appointment":
        return _handle_booking(ticket, bookings, now)
    elif intent == "modify_appointment":
        return _handle_modification(ticket, bookings, now)
    elif intent == "cancel_appointment":
        return _handle_cancellation(ticket, bookings, now)
    elif intent in ["check_appointment", "clarify", "escalate", "reject"]:
        return PolicyResult(action=Action(intent))
    else:
        # Fallback for unknown intents
        return PolicyResult(action=Action.REJECT, message="알 수 없는 요청입니다.")


def _handle_booking(ticket: Ticket, bookings: list[Booking], now: datetime) -> PolicyResult:
    """Handles the logic for new appointment bookings."""
    request_time = ticket.context.get("appointment_time")
    if not request_time:
        return PolicyResult(action=Action.CLARIFY, message="예약 시간이 명시되지 않았습니다.")

    # For new bookings on the same day, the 24-hour rule is not applicable
    # This check is implicitly handled by just checking slot availability.
    
    duration = get_appointment_duration(ticket.user.is_first_visit)
    is_available, reason = is_slot_available(request_time, duration, bookings, now)

    if is_available:
        return PolicyResult(action=Action.BOOK_APPOINTMENT)
    else:
        # Do not suggest alternatives if the request is for a time in the past
        if "과거 시간" in reason:
            return PolicyResult(action=Action.CLARIFY, message=reason, suggested_slots=[])

        alternatives = suggest_alternative_slots(request_time, duration, bookings, now)
        return PolicyResult(
            action=Action.CLARIFY,
            message=reason,
            suggested_slots=alternatives,
        )


def _handle_modification(ticket: Ticket, bookings: list[Booking], now: datetime) -> PolicyResult:
    """Handles the logic for modifying an existing appointment."""
    booking_id = ticket.context.get("booking_id")
    original_booking = next((b for b in bookings if b.booking_id == booking_id), None)

    if not original_booking:
        return PolicyResult(action=Action.REJECT, message="수정할 예약 정보를 찾을 수 없습니다.")

    # Check 24-hour rule
    if not is_change_or_cancel_allowed(original_booking.start_time, now):
        return PolicyResult(
            action=Action.REJECT,
            message="예약 변경은 방문 24시간 이전에만 가능합니다."
        )
    
    new_time = ticket.context.get("new_appointment_time")
    if not new_time:
        return PolicyResult(action=Action.CLARIFY, message="변경할 예약 시간이 명시되지 않았습니다.")

    # Check availability of the new slot
    duration = get_appointment_duration(original_booking.is_first_visit)
    is_available, reason = is_slot_available(new_time, duration, bookings, now, booking_id_to_ignore=booking_id)

    if is_available:
        return PolicyResult(action=Action.MODIFY_APPOINTMENT)
    else:
        alternatives = suggest_alternative_slots(new_time, duration, bookings, now, booking_id_to_ignore=booking_id)
        return PolicyResult(
            action=Action.CLARIFY,
            message=reason,
            suggested_slots=alternatives,
        )

def _handle_cancellation(ticket: Ticket, bookings: list[Booking], now: datetime) -> PolicyResult:
    """Handles the logic for cancelling an existing appointment."""
    booking_id = ticket.context.get("booking_id")
    booking_to_cancel = next((b for b in bookings if b.booking_id == booking_id), None)

    if not booking_to_cancel:
        return PolicyResult(action=Action.REJECT, message="취소할 예약 정보를 찾을 수 없습니다.")

    if not is_change_or_cancel_allowed(booking_to_cancel.start_time, now):
        return PolicyResult(
            action=Action.REJECT,
            message="예약 취소는 방문 24시간 이전에만 가능합니다."
        )
    
    return PolicyResult(action=Action.CANCEL_APPOINTMENT)


def get_appointment_duration(is_first_visit: bool) -> timedelta:
    """
    Determines appointment duration. 40 mins for first visit, 30 for follow-up.
    (F-054)
    """
    return timedelta(minutes=40) if is_first_visit else timedelta(minutes=30)


def is_change_or_cancel_allowed(appointment_time: datetime, now: datetime) -> bool:
    """
    Checks if a change or cancellation is allowed (at least 24 hours before).
    (F-055, F-057)
    """
    return (appointment_time - now).total_seconds() >= 86400


def is_slot_available(
    request_time: datetime,
    duration: timedelta,
    bookings: list[Booking],
    now: datetime,
    booking_id_to_ignore: str | None = None
) -> tuple[bool, str]:
    """
    Checks for slot availability, considering overlaps and capacity.
    (F-053, F-054)
    """
    # Rule: Cannot book appointments in the past.
    if request_time < now:
        return False, "과거 시간으로는 예약할 수 없습니다."
        
    request_start = request_time
    request_end = request_time + duration
    
    # Filter out the booking being modified
    relevant_bookings = [b for b in bookings if b.booking_id != booking_id_to_ignore]

    # 1. Check for capacity (max 3 at the same start time)
    same_time_bookings = sum(1 for b in relevant_bookings if b.start_time == request_start)
    if same_time_bookings >= 3:
        return False, "해당 시간의 예약 정원(3명)이 모두 찼습니다."

    # 2. Check for overlaps
    for booking in relevant_bookings:
        existing_start = booking.start_time
        existing_end = booking.end_time

        # Check for overlap (1 minute is enough to be a conflict)
        if max(request_start, existing_start) < min(request_end, existing_end):
            return False, "해당 시간은 다른 예약과 겹칩니다."

    return True, ""


def suggest_alternative_slots(
    original_time: datetime,
    duration: timedelta,
    bookings: list[Booking],
    now: datetime,
    booking_id_to_ignore: str | None = None
) -> list[datetime]:
    """
    Suggests 1-3 available alternative slots on the same day.
    (F-056)
    """
    suggestions = []
    
    # Define clinic hours (e.g., 9 AM to 6 PM)
    day_start = datetime.combine(original_time.date(), time(9, 0))
    day_end = datetime.combine(original_time.date(), time(18, 0))
    
    # Start checking from the next 30-minute interval after the original failed time
    current_time = original_time + timedelta(minutes=30 - original_time.minute % 30)


    while current_time < day_end and len(suggestions) < 3:
        # Ensure we don't suggest times in the past
        if current_time < now:
            current_time += timedelta(minutes=30)
            continue

        is_available, _ = is_slot_available(current_time, duration, bookings, now, booking_id_to_ignore)
        if is_available:
            suggestions.append(current_time)
        
        current_time += timedelta(minutes=30) # Check every half hour

    return suggestions
