
import pytest
from datetime import datetime, timezone
from freezegun import freeze_time
from src.policy import apply_policy, POLICY_REASONS

# --- Mock Data ---
# A list of existing appointments for testing collision and capacity.
# Note: Times are in UTC.
EXISTING_APPOINTMENTS_SAMPLE = [
    {
        "id": "appt-001",
        "customer_id": "user-123",
        "booking_time": "2026-04-10T14:00:00Z", # 2:00 PM UTC
        "customer_type": "재진" # 30 mins
    },
    {
        "id": "appt-002",
        "customer_id": "user-456",
        "booking_time": "2026-04-10T14:30:00Z", # 2:30 PM UTC
        "customer_type": "초진" # 40 mins
    },
    {
        "id": "appt-003",
        "customer_id": "user-789",
        "booking_time": "2026-04-10T15:00:00Z", # 3:00 PM UTC
        "customer_type": "재진" # 30 mins
    },
    {
        "id": "appt-004",
        "customer_id": "user-101",
        "booking_time": "2026-04-10T14:00:00Z", # Another one at 2:00 PM UTC
        "customer_type": "재진" # 30 mins
    }
]

# --- F-009: Test Max 3 People Per Hour ---
@freeze_time("2026-04-08T12:00:00Z") # Well before appointments
def test_F009_reject_4th_appointment_in_hour():
    # There are already 2 appointments in the 14:00-15:00 window (appt-001, appt-004)
    # and one starting at 14:30 (appt-002), making 3 total in that hour.
    intent = {
        "action": "book_appointment",
        "booking_time": "2026-04-10T14:15:00Z", # Attempt to book at 2:15 PM
        "customer_type": "재진"
    }
    result = apply_policy(intent, None, EXISTING_APPOINTMENTS_SAMPLE, datetime.now(timezone.utc))
    assert not result["allowed"]
    assert result["reason"] == POLICY_REASONS["SLOT_FULL_CAPACITY"]

# --- F-010: Test 24-Hour Change/Cancel Rule ---
@freeze_time("2026-04-09T14:00:00Z") # Exactly 24 hours before
def test_F010_allow_change_exactly_24_hours_before():
    intent = {"action": "modify_appointment", "booking_time": "2026-04-10T16:00:00Z"} # Changed to a free slot
    existing_appt = EXISTING_APPOINTMENTS_SAMPLE[0] # Booking at 2026-04-10T14:00:00Z
    result = apply_policy(intent, existing_appt, EXISTING_APPOINTMENTS_SAMPLE, datetime.now(timezone.utc))
    # This should be allowed because the *new* slot is available, and the *old* one is cancellable
    assert result["allowed"]

@freeze_time("2026-04-09T14:00:01Z") # 23 hours, 59 mins, 59 secs before
def test_F010_reject_change_less_than_24_hours_before():
    intent = {"action": "cancel_appointment"}
    existing_appt = EXISTING_APPOINTMENTS_SAMPLE[0] # Booking at 2026-04-10T14:00:00Z
    result = apply_policy(intent, existing_appt, EXISTING_APPOINTMENTS_SAMPLE, datetime.now(timezone.utc))
    assert not result["allowed"]
    assert result["reason"] == POLICY_REASONS["CHANGE_WINDOW_EXPIRED"]

# --- F-011: Test Slot Duration (New vs. Returning) ---
@freeze_time("2026-04-08T12:00:00Z")
def test_F011_slot_duration_and_collision():
    # appt-001 is 14:00-14:30.
    # A new patient '초진' (40 mins) at 13:50 should collide with it.
    intent_new_patient = {
        "action": "book_appointment",
        "booking_time": "2026-04-10T13:50:00Z", # Ends at 14:30
        "customer_type": "초진"
    }
    result = apply_policy(intent_new_patient, None, EXISTING_APPOINTMENTS_SAMPLE, datetime.now(timezone.utc))
    assert not result["allowed"]
    assert result["reason"] == POLICY_REASONS["SLOT_UNAVAILABLE"]
    
    # A returning patient '재진' (30 mins) at 13:30 should fit.
    intent_returning_patient = {
        "action": "book_appointment",
        "booking_time": "2026-04-10T13:30:00Z", # Ends at 14:00, no collision
        "customer_type": "재진"
    }
    result = apply_policy(intent_returning_patient, None, EXISTING_APPOINTMENTS_SAMPLE, datetime.now(timezone.utc))
    assert result["allowed"]

# --- F-012: Test Need for Existing Appointment ---
@freeze_time("2026-04-08T12:00:00Z")
def test_F012_reject_modify_without_existing_appointment():
    intent = {"action": "modify_appointment"}
    result = apply_policy(intent, None, EXISTING_APPOINTMENTS_SAMPLE, datetime.now(timezone.utc))
    assert not result["allowed"]
    assert result["reason"] == POLICY_REASONS["NO_EXISTING_APPOINTMENT"]

@freeze_time("2026-04-08T12:00:00Z")
def test_F012_reject_cancel_without_existing_appointment():
    intent = {"action": "cancel_appointment"}
    result = apply_policy(intent, None, EXISTING_APPOINTMENTS_SAMPLE, datetime.now(timezone.utc))
    assert not result["allowed"]
    assert result["reason"] == POLICY_REASONS["NO_EXISTING_APPOINTMENT"]

@freeze_time("2026-04-08T12:00:00Z")
def test_F012_reject_check_without_existing_appointment():
    intent = {"action": "check_appointment"}
    result = apply_policy(intent, None, EXISTING_APPOINTMENTS_SAMPLE, datetime.now(timezone.utc))
    assert not result["allowed"]
    assert result["reason"] == POLICY_REASONS["NO_EXISTING_APPOINTMENT"]

@freeze_time("2026-04-08T12:00:00Z")
def test_F012_allow_check_with_existing_appointment():
    intent = {"action": "check_appointment"}
    existing_appt = EXISTING_APPOINTMENTS_SAMPLE[0]
    result = apply_policy(intent, existing_appt, EXISTING_APPOINTMENTS_SAMPLE, datetime.now(timezone.utc))
    assert result["allowed"]
