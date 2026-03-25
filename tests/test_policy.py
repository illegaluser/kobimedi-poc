from datetime import datetime, timezone

from freezegun import freeze_time

from src.policy import (
    POLICY_REASONS,
    apply_policy,
    get_appointment_duration,
    is_change_allowed,
    is_slot_available,
    suggest_alternative_slots,
    validate_existing_appointment,
)


class FakeStorage:
    def __init__(self, bookings):
        self.bookings = list(bookings)
        self.calls = []

    def find_bookings(self, customer_name=None, filters=None, patient_contact=None):
        self.calls.append(
            {
                "customer_name": customer_name,
                "filters": filters or {},
                "patient_contact": patient_contact,
            }
        )
        filters = filters or {}
        results = []
        for booking in self.bookings:
            if patient_contact and booking.get("patient_contact") != patient_contact:
                continue
            if customer_name and booking.get("customer_name") != customer_name:
                continue
            if filters.get("id") and booking.get("id") != filters["id"]:
                continue
            if filters.get("department") and booking.get("department") != filters["department"]:
                continue
            if filters.get("booking_time") and booking.get("booking_time") != filters["booking_time"]:
                continue
            if filters.get("date") and not str(booking.get("booking_time", "")).startswith(filters["date"]):
                continue
            if filters.get("time") and filters["time"] not in str(booking.get("booking_time", "")):
                continue
            results.append(booking)
        return results


def test_F040_policy_functions_use_explicit_now_parameter_for_boundary_judgment():
    now = datetime.fromisoformat("2025-03-17T09:00:00+09:00")

    assert is_change_allowed("2025-03-18T09:00:00+09:00", now) is True
    assert is_change_allowed("2025-03-18T08:59:00+09:00", now) is False

    allowed_result = apply_policy(
        {"action": "book_appointment", "booking_time": "2025-03-17T15:00:00+09:00", "customer_type": "재진"},
        None,
        [],
        now,
    )
    blocked_result = apply_policy(
        {"action": "book_appointment", "booking_time": "2025-03-17T08:50:00+09:00", "customer_type": "재진"},
        None,
        [],
        now,
    )

    assert allowed_result["allowed"] is True
    assert blocked_result["allowed"] is False
    assert blocked_result["reason_code"] == "PAST_BOOKING_TIME"


@freeze_time("2026-03-25T09:00:00+09:00")
def test_F051_F052_business_hours_and_lunch_rules_are_deterministic():
    now = datetime.now(timezone.utc)

    lunch_start_result = apply_policy(
        {"action": "book_appointment", "booking_time": "2026-03-26T12:30:00+09:00", "customer_type": "재진"},
        None,
        [],
        now,
    )
    lunch_overlap_result = apply_policy(
        {"action": "book_appointment", "booking_time": "2026-03-26T12:20:00+09:00", "customer_type": "초진"},
        None,
        [],
        now,
    )
    lunch_end_result = apply_policy(
        {"action": "book_appointment", "booking_time": "2026-03-26T13:30:00+09:00", "customer_type": "재진"},
        None,
        [],
        now,
    )
    weekday_close_result = apply_policy(
        {"action": "book_appointment", "booking_time": "2026-03-26T18:00:00+09:00", "customer_type": "재진"},
        None,
        [],
        now,
    )
    saturday_close_result = apply_policy(
        {"action": "book_appointment", "booking_time": "2026-03-28T13:00:00+09:00", "customer_type": "재진"},
        None,
        [],
        now,
    )
    sunday_closed_result = apply_policy(
        {"action": "book_appointment", "booking_time": "2026-03-29T10:00:00+09:00", "customer_type": "재진"},
        None,
        [],
        now,
    )

    assert lunch_start_result["reason_code"] == "LUNCH_BREAK"
    assert lunch_overlap_result["reason_code"] == "LUNCH_BREAK"
    assert lunch_end_result["allowed"] is True
    assert weekday_close_result["reason_code"] == "OUTSIDE_BUSINESS_HOURS"
    assert saturday_close_result["reason_code"] == "OUTSIDE_BUSINESS_HOURS"
    assert sunday_closed_result["reason_code"] == "CLOSED_SUNDAY"


@freeze_time("2026-03-25T09:00:00+09:00")
def test_F052_public_holiday_uncertain_returns_clarify_without_guessing():
    result = apply_policy(
        {
            "action": "book_appointment",
            "booking_time": "2026-04-01T10:00:00+09:00",
            "customer_type": "재진",
            "holiday_status": "unknown",
        },
        None,
        [],
        datetime.now(timezone.utc),
    )

    assert result["allowed"] is False
    assert result["reason_code"] == "HOLIDAY_UNCERTAIN"
    assert result["recommended_action"] == "clarify"


@freeze_time("2026-04-08T12:00:00Z")
def test_F053_hourly_capacity_allows_third_patient_but_blocks_fourth():
    third_result = apply_policy(
        {
            "action": "book_appointment",
            "booking_time": "2026-04-10T14:50:00Z",
            "customer_type": "재진",
        },
        None,
        [
            {"id": "appt-001", "booking_time": "2026-04-10T14:00:00Z", "customer_type": "재진"},
            {"id": "appt-002", "booking_time": "2026-04-10T14:20:00Z", "customer_type": "재진"},
        ],
        datetime.now(timezone.utc),
    )
    fourth_result = apply_policy(
        {
            "action": "book_appointment",
            "booking_time": "2026-04-10T14:55:00Z",
            "customer_type": "재진",
        },
        None,
        [
            {"id": "appt-001", "booking_time": "2026-04-10T14:00:00Z", "customer_type": "재진"},
            {"id": "appt-002", "booking_time": "2026-04-10T14:20:00Z", "customer_type": "재진"},
            {"id": "appt-003", "booking_time": "2026-04-10T14:40:00Z", "customer_type": "재진"},
        ],
        datetime.now(timezone.utc),
    )

    assert third_result["allowed"] is True
    assert fourth_result["allowed"] is False
    assert fourth_result["reason_code"] == "SLOT_FULL_CAPACITY"
    assert fourth_result["needs_alternative"] is True
    assert 1 <= len(fourth_result["alternative_slots"]) <= 3


@freeze_time("2026-04-08T12:00:00Z")
def test_F054_first_visit_uses_40_minutes_and_returning_visit_uses_30_minutes():
    appointments = [{"id": "appt-a", "booking_time": "2026-04-10T14:00:00Z", "customer_type": "재진"}]

    first_visit_result = apply_policy(
        {
            "action": "book_appointment",
            "booking_time": "2026-04-10T13:50:00Z",
            "customer_type": "초진",
        },
        None,
        appointments,
        datetime.now(timezone.utc),
    )
    returning_visit_result = apply_policy(
        {
            "action": "book_appointment",
            "booking_time": "2026-04-10T13:30:00Z",
            "customer_type": "재진",
        },
        None,
        appointments,
        datetime.now(timezone.utc),
    )

    assert first_visit_result["allowed"] is False
    assert first_visit_result["reason_code"] == "SLOT_UNAVAILABLE"
    assert first_visit_result["slot_duration_minutes"] == 40
    assert returning_visit_result["allowed"] is True
    assert returning_visit_result["slot_duration_minutes"] == 30
    assert get_appointment_duration("초진") == 40
    assert get_appointment_duration("재진") == 30
    assert get_appointment_duration(None) is None


@freeze_time("2025-03-16T09:00:00+09:00")
def test_F055_exactly_24_hours_before_modify_is_allowed():
    existing_appointment = {
        "id": "existing-001",
        "booking_time": "2025-03-17T09:00:00+09:00",
        "customer_type": "재진",
    }

    result = apply_policy(
        {
            "action": "modify_appointment",
            "booking_time": "2025-03-18T10:00:00+09:00",
        },
        existing_appointment,
        [existing_appointment],
        datetime.now(timezone.utc),
    )

    assert result["allowed"] is True
    assert result["reason_code"] == "SUCCESS"


@freeze_time("2025-03-16T09:01:00+09:00")
def test_F055_less_than_24_hours_before_modify_or_cancel_is_blocked():
    existing_modify = {
        "id": "existing-002",
        "booking_time": "2025-03-17T09:00:00+09:00",
        "customer_type": "재진",
    }
    existing_cancel = {
        "id": "existing-003",
        "booking_time": "2025-03-17T09:00:00+09:00",
        "customer_type": "재진",
    }

    modify_result = apply_policy(
        {
            "action": "modify_appointment",
            "booking_time": "2025-03-18T10:00:00+09:00",
        },
        existing_modify,
        [existing_modify],
        datetime.now(timezone.utc),
    )
    cancel_result = apply_policy(
        {"action": "cancel_appointment"},
        existing_cancel,
        [existing_cancel],
        datetime.now(timezone.utc),
    )

    assert modify_result["allowed"] is False
    assert cancel_result["allowed"] is False
    assert modify_result["reason_code"] == "CHANGE_WINDOW_EXPIRED"
    assert cancel_result["reason_code"] == "CHANGE_WINDOW_EXPIRED"


@freeze_time("2025-03-17T09:00:00+09:00")
def test_F055_does_not_apply_24_hour_rule_to_general_same_day_new_booking():
    result = apply_policy(
        {
            "action": "book_appointment",
            "booking_time": "2025-03-17T15:00:00+09:00",
            "customer_type": "재진",
        },
        None,
        [],
        datetime.now(timezone.utc),
    )

    assert result["allowed"] is True
    assert result["reason_code"] == "SUCCESS"
    assert result["same_day"] is True


@freeze_time("2025-03-16T09:00:00+09:00")
def test_F057_same_day_emergency_new_booking_escalates():
    result = apply_policy(
        {
            "action": "book_appointment",
            "booking_time": "2025-03-16T15:00:00+09:00",
            "customer_type": "재진",
            "is_emergency": True,
        },
        None,
        [],
        datetime.now(timezone.utc),
    )

    assert result["allowed"] is False
    assert result["reason_code"] == "SAME_DAY_EMERGENCY_ESCALATION"
    assert result["recommended_action"] == "escalate"


@freeze_time("2026-04-08T12:00:00Z")
def test_F056_rejection_returns_one_to_three_alternative_slots():
    appointments = [{"id": "appt-b", "booking_time": "2026-04-10T14:00:00Z", "customer_type": "재진"}]

    result = apply_policy(
        {
            "action": "book_appointment",
            "booking_time": "2026-04-10T14:10:00Z",
            "customer_type": "재진",
        },
        None,
        appointments,
        datetime.now(timezone.utc),
    )

    assert result["allowed"] is False
    assert result["reason_code"] == "SLOT_UNAVAILABLE"
    assert result["needs_alternative"] is True
    assert 1 <= len(result["alternative_slots"]) <= 3
    assert result["alternative_slots"][0] == "2026-04-10T14:30:00Z"


@freeze_time("2026-04-08T12:00:00+09:00")
def test_existing_appointment_lookup_uses_storage_as_source_of_truth():
    storage = FakeStorage(
        [
            {
                "id": "booking-001",
                "customer_name": "김민수",
                "patient_contact": "010-1111-2222",
                "department": "이비인후과",
                "booking_time": "2026-04-10T14:00:00+09:00",
                "customer_type": "재진",
            }
        ]
    )

    for action in ["modify_appointment", "cancel_appointment", "check_appointment"]:
        result = apply_policy(
            {
                "action": action,
                "customer_name": "김민수",
                "patient_contact": "010-1111-2222",
                "department": "이비인후과",
                "date": "2026-04-10",
                "time": "14:00",
                "booking_time": "2026-04-10T14:00:00+09:00",
                "customer_type": "재진",
            },
            None,
            [],
            datetime.now(timezone.utc),
            storage=storage,
        )

        assert result["allowed"] is True
        assert result["recommended_action"] == action

    assert len(storage.calls) == 3
    assert all(call["customer_name"] == "김민수" for call in storage.calls)
    assert all(call["patient_contact"] == "010-1111-2222" for call in storage.calls)


def test_validate_existing_appointment_returns_clarify_for_ambiguous_candidates():
    result = validate_existing_appointment(
        "modify_appointment",
        None,
        candidate_appointments=[
            {"id": "appt-1", "booking_time": "2026-04-10T14:00:00Z"},
            {"id": "appt-2", "booking_time": "2026-04-11T14:00:00Z"},
        ],
    )

    assert result["allowed"] is False
    assert result["reason_code"] == "AMBIGUOUS_EXISTING_APPOINTMENT"
    assert result["recommended_action"] == "clarify"


@freeze_time("2026-04-08T12:00:00Z")
def test_is_slot_available_checks_hours_capacity_and_overlap_without_llm():
    now = datetime.now(timezone.utc)
    appointments = [
        {"id": "appt-1", "booking_time": "2026-04-10T14:00:00Z", "customer_type": "재진"},
        {"id": "appt-2", "booking_time": "2026-04-10T14:20:00Z", "customer_type": "재진"},
        {"id": "appt-3", "booking_time": "2026-04-10T14:40:00Z", "customer_type": "재진"},
    ]

    assert is_slot_available("2026-04-10T12:30:00Z", "재진", [], now)[0] is False
    assert is_slot_available("2026-04-10T14:10:00Z", "재진", appointments[:1], now)[0] is False
    assert is_slot_available("2026-04-10T14:50:00Z", "재진", appointments, now)[0] is False
    assert is_slot_available("2026-04-10T15:10:00Z", "재진", appointments, now)[0] is True


@freeze_time("2026-04-08T12:00:00Z")
def test_suggest_alternative_slots_returns_future_candidates_only():
    now = datetime.now(timezone.utc)
    appointments = [{"id": "appt-1", "booking_time": "2026-04-10T14:00:00Z", "customer_type": "재진"}]

    alternatives = suggest_alternative_slots(
        "2026-04-10T14:10:00Z",
        "재진",
        appointments,
        now=now,
    )

    assert 1 <= len(alternatives) <= 3
    assert alternatives[0] == "2026-04-10T14:30:00Z"
    assert all(datetime.fromisoformat(slot.replace("Z", "+00:00")) > datetime.fromisoformat("2026-04-10T14:10:00+00:00") for slot in alternatives)