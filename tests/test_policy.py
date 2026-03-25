from datetime import datetime, timedelta
import pytest
from freezegun import freeze_time

from src.models import Ticket, Booking, User, Action, PolicyResult
from src.policy import (
    apply_policy,
    get_appointment_duration,
    is_change_or_cancel_allowed,
    is_slot_available,
    suggest_alternative_slots,
)

# A fixed point in time for consistent testing
NOW = datetime(2026, 3, 25, 10, 0, 0)

# Mock Data using the new data classes
@pytest.fixture
def sample_user() -> User:
    return User(patient_id="p001", name="김민준", is_first_visit=False)

@pytest.fixture
def new_user() -> User:
    return User(patient_id="p002", name="이서아", is_first_visit=True)

@pytest.fixture
def existing_bookings() -> list[Booking]:
    return [
        # Capacity test: 3 bookings at 2 PM
        Booking(
            booking_id="b001", patient_id="p101", patient_name="박서준",
            start_time=datetime(2026, 3, 26, 14, 0), end_time=datetime(2026, 3, 26, 14, 30),
            is_first_visit=False
        ),
        Booking(
            booking_id="b002", patient_id="p102", patient_name="최지우",
            start_time=datetime(2026, 3, 26, 14, 0), end_time=datetime(2026, 3, 26, 14, 30),
            is_first_visit=False
        ),
        Booking(
            booking_id="b003", patient_id="p103", patient_name="강하늘",
            start_time=datetime(2026, 3, 26, 14, 0), end_time=datetime(2026, 3, 26, 14, 30),
            is_first_visit=False
        ),
        # Overlap test: A 40-minute booking at 3 PM
        Booking(
            booking_id="b004", patient_id="p104", patient_name="윤보라",
            start_time=datetime(2026, 3, 26, 15, 0), end_time=datetime(2026, 3, 26, 15, 40),
            is_first_visit=True
        ),
        # Change/Cancel test: A booking more than 24h away
        Booking(
            booking_id="b005", patient_id="p001", patient_name="김민준",
            start_time=datetime(2026, 3, 27, 11, 0), end_time=datetime(2026, 3, 27, 11, 30),
            is_first_visit=False
        ),
        # Change/Cancel test: A booking less than 24h away
         Booking(
            booking_id="b006", patient_id="p001", patient_name="김민준",
            start_time=datetime(2026, 3, 25, 11, 0), end_time=datetime(2026, 3, 25, 11, 30),
            is_first_visit=False
        )
    ]

# === Test Core Policy Functions ===

@freeze_time(NOW)
def test_book_appointment_success(sample_user, existing_bookings):
    """F-051: Test successful booking in an available slot."""
    ticket = Ticket(
        intent="book_appointment",
        user=sample_user,
        context={"appointment_time": datetime(2026, 3, 26, 16, 0)}
    )
    result = apply_policy(ticket, existing_bookings, NOW)
    assert result.action == Action.BOOK_APPOINTMENT

@freeze_time(NOW)
def test_book_appointment_fail_past(sample_user, existing_bookings):
    """F-040: Test failure when booking in the past."""
    ticket = Ticket(
        intent="book_appointment",
        user=sample_user,
        context={"appointment_time": datetime(2026, 3, 25, 9, 0)} # In the past
    )
    result = apply_policy(ticket, existing_bookings, NOW)
    assert result.action == Action.CLARIFY
    assert "과거 시간" in result.message
    assert not result.suggested_slots # Should not suggest past slots

@freeze_time(NOW)
def test_book_appointment_fail_overlap_new_patient(new_user, existing_bookings):
    """F-054: Test failure due to overlap with an existing booking (40 min duration)."""
    ticket = Ticket(
        intent="book_appointment",
        user=new_user,
        # This 40-min booking would end at 15:10, overlapping with b004 (starts at 15:00)
        context={"appointment_time": datetime(2026, 3, 26, 14, 30)}
    )
    result = apply_policy(ticket, existing_bookings, NOW)
    assert result.action == Action.CLARIFY
    assert "다른 예약과 겹칩니다" in result.message

@freeze_time(NOW)
def test_book_appointment_fail_capacity(sample_user, existing_bookings):
    """F-053: Test failure due to reaching capacity limit (3)."""
    ticket = Ticket(
        intent="book_appointment",
        user=sample_user,
        context={"appointment_time": datetime(2026, 3, 26, 14, 0)} # 3 bookings already exist
    )
    result = apply_policy(ticket, existing_bookings, NOW)
    assert result.action == Action.CLARIFY
    assert "예약 정원" in result.message

@freeze_time(NOW)
def test_book_appointment_suggests_alternatives(sample_user, existing_bookings):
    """F-056: Test if a failed booking suggests alternative slots."""
    ticket = Ticket(
        intent="book_appointment",
        user=sample_user,
        context={"appointment_time": datetime(2026, 3, 26, 14, 0)} # Full slot
    )
    result = apply_policy(ticket, existing_bookings, NOW)
    assert result.action == Action.CLARIFY
    assert len(result.suggested_slots) > 0
    assert len(result.suggested_slots) <= 3
    # First suggestion should be after the failed 14:00 slot
    assert result.suggested_slots[0] > datetime(2026, 3, 26, 14, 0)

@freeze_time(NOW)
def test_modify_appointment_success(sample_user, existing_bookings):
    """F-055: Test successful modification >24 hours before."""
    ticket = Ticket(
        intent="modify_appointment",
        user=sample_user,
        context={
            "booking_id": "b005", # Original time is 2026-03-27 11:00
            "new_appointment_time": datetime(2026, 3, 27, 16, 0)
        }
    )
    result = apply_policy(ticket, existing_bookings, NOW)
    assert result.action == Action.MODIFY_APPOINTMENT

@freeze_time(NOW)
def test_modify_appointment_fail_window_expired(sample_user, existing_bookings):
    """F-055: Test failed modification <24 hours before."""
    ticket = Ticket(
        intent="modify_appointment",
        user=sample_user,
        context={
            "booking_id": "b006", # Original time is 2026-03-25 11:00 (1h from NOW)
            "new_appointment_time": datetime(2026, 3, 25, 16, 0)
        }
    )
    result = apply_policy(ticket, existing_bookings, NOW)
    assert result.action == Action.REJECT
    assert "24시간 이전에만 가능" in result.message

@freeze_time(NOW)
def test_modify_appointment_ignores_own_slot(sample_user, existing_bookings):
    """F-054: Test that modification check ignores the original booking slot."""
    ticket = Ticket(
        intent="modify_appointment",
        user=sample_user,
        context={
            "booking_id": "b005", # Original time: 2026-03-27 11:00
            # Try to move it 30 mins earlier, into a free slot.
            # Without ignoring b005, this might seem blocked by itself.
            "new_appointment_time": datetime(2026, 3, 27, 10, 30)
        }
    )
    result = apply_policy(ticket, existing_bookings, NOW)
    assert result.action == Action.MODIFY_APPOINTMENT

@freeze_time(NOW)
def test_cancel_appointment_success(sample_user, existing_bookings):
    """F-055: Test successful cancellation >24 hours before."""
    ticket = Ticket(
        intent="cancel_appointment",
        user=sample_user,
        context={"booking_id": "b005"} # Original time is 2026-03-27 11:00
    )
    result = apply_policy(ticket, existing_bookings, NOW)
    assert result.action == Action.CANCEL_APPOINTMENT

@freeze_time(NOW)
def test_cancel_appointment_fail_window_expired(sample_user, existing_bookings):
    """F-055: Test failed cancellation <24 hours before."""
    ticket = Ticket(
        intent="cancel_appointment",
        user=sample_user,
        context={"booking_id": "b006"} # Original time is 2026-03-25 11:00
    )
    result = apply_policy(ticket, existing_bookings, NOW)
    assert result.action == Action.REJECT
    assert "24시간 이전에만 가능" in result.message

def test_same_day_new_booking_is_not_subject_to_24h_rule(sample_user, existing_bookings):
    """F-057: Same-day new bookings are allowed if slots are free, ignoring the 24h rule concept."""
    now = datetime(2026, 3, 26, 9, 0) # Morning of the 26th
    ticket = Ticket(
        intent="book_appointment",
        user=sample_user,
        context={"appointment_time": datetime(2026, 3, 26, 17, 0)} # Booking for later today
    )
    result = apply_policy(ticket, existing_bookings, now)
    assert result.action == Action.BOOK_APPOINTMENT

# === Test Helper Functions ===

def test_get_appointment_duration():
    """F-054: Test duration calculation."""
    assert get_appointment_duration(is_first_visit=True) == timedelta(minutes=40)
    assert get_appointment_duration(is_first_visit=False) == timedelta(minutes=30)

def test_is_change_or_cancel_allowed():
    """F-055: Test the 24-hour boundary condition precisely."""
    now = datetime(2026, 3, 25, 10, 0, 0)
    
    # Exactly 24 hours - allowed
    appt_time_ok = datetime(2026, 3, 26, 10, 0, 0)
    assert is_change_or_cancel_allowed(appt_time_ok, now) is True

    # 23 hours 59 minutes 59 seconds - not allowed
    appt_time_fail = datetime(2026, 3, 26, 9, 59, 59)
    assert is_change_or_cancel_allowed(appt_time_fail, now) is False
    
    # Well over 24 hours - allowed
    appt_time_far = datetime(2026, 3, 27, 10, 0, 0)
    assert is_change_or_cancel_allowed(appt_time_far, now) is True

def test_suggest_alternative_slots_logic(existing_bookings):
    """F-056: Test alternative slot suggestion logic."""
    now = datetime(2026, 3, 26, 13, 0)
    # Requesting 14:00, which is full
    original_time = datetime(2026, 3, 26, 14, 0)
    duration = timedelta(minutes=30)
    
    suggestions = suggest_alternative_slots(original_time, duration, existing_bookings, now)
    
    # Expected: 14:00 is full. The next available 30-min slot is 14:30.
    # The booking at 15:00-15:40 (b004) does not conflict with a 14:30-15:00 slot.
    assert len(suggestions) > 0
    assert suggestions[0] == datetime(2026, 3, 26, 14, 30)
