
from datetime import datetime, timezone

from freezegun import freeze_time

from src.policy import POLICY_REASONS, apply_policy, get_appointment_duration, validate_existing_appointment


class FakeStorage:
    def __init__(self, bookings):
        self.bookings = list(bookings)
        self.calls = []

    def find_bookings(self, customer_name=None, filters=None):
        self.calls.append({"customer_name": customer_name, "filters": filters or {}})
        results = []
        filters = filters or {}
        for booking in self.bookings:
            if customer_name and booking.get("customer_name") != customer_name:
                continue
            if filters.get("id") and booking.get("id") != filters["id"]:
                continue
            if filters.get("department") and booking.get("department") != filters["department"]:
                continue
            if filters.get("booking_time") and booking.get("booking_time") != filters["booking_time"]:
                continue
            if filters.get("date") and not booking.get("booking_time", "").startswith(filters["date"]):
                continue
            if filters.get("time") and filters["time"] not in booking.get("booking_time", ""):
                continue
            results.append(booking)
        return results


EXISTING_APPOINTMENTS_SAMPLE = [
    {
        "id": "appt-001",
        "customer_id": "user-123",
        "booking_time": "2026-04-10T14:00:00Z",
        "customer_type": "재진",
    },
    {
        "id": "appt-002",
        "customer_id": "user-456",
        "booking_time": "2026-04-10T14:20:00Z",
        "customer_type": "재진",
    },
    {
        "id": "appt-003",
        "customer_id": "user-789",
        "booking_time": "2026-04-10T14:40:00Z",
        "customer_type": "재진",
    },
    {
        "id": "appt-004",
        "customer_id": "user-999",
        "booking_time": "2026-04-10T16:00:00Z",
        "customer_type": "초진",
    },
]


@freeze_time("2026-04-08T12:00:00Z")
def test_F015_hourly_capacity_allows_third_patient_but_blocks_fourth():
    third_result = apply_policy(
        {
            "action": "book_appointment",
            "booking_time": "2026-04-10T14:50:00Z",
            "customer_type": "재진",
        },
        None,
        EXISTING_APPOINTMENTS_SAMPLE[:2],
        datetime.now(timezone.utc),
    )

    assert third_result["allowed"] is True
    assert third_result["reason"] == POLICY_REASONS["SUCCESS"]

    fourth_result = apply_policy(
        {
            "action": "book_appointment",
            "booking_time": "2026-04-10T14:55:00Z",
            "customer_type": "재진",
        },
        None,
        EXISTING_APPOINTMENTS_SAMPLE[:3],
        datetime.now(timezone.utc),
    )

    assert fourth_result["allowed"] is False
    assert fourth_result["reason"] == POLICY_REASONS["SLOT_FULL_CAPACITY"]
    assert fourth_result["needs_alternative"] is True
    assert len(fourth_result["alternative_slots"]) >= 1


@freeze_time("2025-03-16T09:00:00+09:00")
def test_F016_same_day_modification_boundary_exactly_24_hours_allowed():
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
    assert result["reason"] == POLICY_REASONS["SUCCESS"]


@freeze_time("2025-03-16T09:01:00+09:00")
def test_F016_same_day_modification_rejected_at_23_hours_59_minutes():
    existing_appointment = {
        "id": "existing-002",
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

    assert result["allowed"] is False
    assert result["reason"] == POLICY_REASONS["CHANGE_WINDOW_EXPIRED"]


@freeze_time("2025-03-16T09:00:00+09:00")
def test_F016_cancel_rejected_when_less_than_24_hours_remain():
    existing_appointment = {
        "id": "existing-003",
        "booking_time": "2025-03-16T15:00:00+09:00",
        "customer_type": "재진",
    }

    result = apply_policy(
        {"action": "cancel_appointment"},
        existing_appointment,
        [existing_appointment],
        datetime.now(timezone.utc),
    )

    assert result["allowed"] is False
    assert result["reason"] == POLICY_REASONS["CHANGE_WINDOW_EXPIRED"]


@freeze_time("2026-04-08T12:00:00Z")
def test_F017_slot_duration_applies_for_first_visit_and_returning_visit():
    appointments = [
        {
            "id": "appt-a",
            "booking_time": "2026-04-10T14:00:00Z",
            "customer_type": "재진",
        }
    ]

    new_patient_result = apply_policy(
        {
            "action": "book_appointment",
            "booking_time": "2026-04-10T13:50:00Z",
            "customer_type": "초진",
        },
        None,
        appointments,
        datetime.now(timezone.utc),
    )
    assert new_patient_result["allowed"] is False
    assert new_patient_result["reason"] == POLICY_REASONS["SLOT_UNAVAILABLE"]
    assert new_patient_result["slot_duration_minutes"] == 40

    returning_patient_result = apply_policy(
        {
            "action": "book_appointment",
            "booking_time": "2026-04-10T13:30:00Z",
            "customer_type": "재진",
        },
        None,
        appointments,
        datetime.now(timezone.utc),
    )
    assert returning_patient_result["allowed"] is True
    assert returning_patient_result["slot_duration_minutes"] == 30

    assert get_appointment_duration("초진") == 40
    assert get_appointment_duration("재진") == 30
    assert get_appointment_duration(None) is None


@freeze_time("2026-04-08T12:00:00Z")
def test_F018_modify_cancel_check_without_existing_appointment_require_clarify():
    for action in ["modify_appointment", "cancel_appointment", "check_appointment"]:
        result = apply_policy(
            {"action": action},
            None,
            EXISTING_APPOINTMENTS_SAMPLE,
            datetime.now(timezone.utc),
        )

        assert result["allowed"] is False
        assert result["reason"] == POLICY_REASONS["NO_EXISTING_APPOINTMENT"]
        assert result["recommended_action"] == "clarify"


def test_F018_ambiguous_existing_appointment_returns_clarify():
    result = validate_existing_appointment(
        "modify_appointment",
        None,
        candidate_appointments=[
            {"id": "appt-1", "booking_time": "2026-04-10T14:00:00Z"},
            {"id": "appt-2", "booking_time": "2026-04-11T14:00:00Z"},
        ],
    )

    assert result["allowed"] is False
    assert result["reason"] == POLICY_REASONS["AMBIGUOUS_EXISTING_APPOINTMENT"]
    assert result["recommended_action"] == "clarify"


@freeze_time("2026-04-08T12:00:00+09:00")
def test_F018_modify_cancel_check_use_storage_find_bookings_as_source_of_truth():
    storage = FakeStorage(
        [
            {
                "id": "booking-001",
                "customer_name": "김민수",
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
    assert all(call["filters"]["department"] == "이비인후과" for call in storage.calls)


@freeze_time("2026-04-08T12:00:00+09:00")
def test_F018_storage_truth_overrides_missing_or_stale_context_hint():
    storage = FakeStorage([])

    result = apply_policy(
        {
            "action": "check_appointment",
            "customer_name": "김민수",
            "department": "이비인후과",
            "date": "2026-04-10",
            "time": "14:00",
            "booking_time": "2026-04-10T14:00:00+09:00",
        },
        {
            "id": "stale-context-booking",
            "customer_name": "김민수",
            "department": "이비인후과",
            "booking_time": "2026-04-10T14:00:00+09:00",
            "customer_type": "재진",
        },
        [],
        datetime.now(timezone.utc),
        storage=storage,
    )

    assert result["allowed"] is False
    assert result["reason"] == POLICY_REASONS["NO_EXISTING_APPOINTMENT"]
    assert result["recommended_action"] == "clarify"


def test_F040_policy_time_judgment_uses_explicit_now_parameter_without_freezegun():
    now = datetime.fromisoformat("2025-03-16T09:00:00+09:00")

    allowed_result = apply_policy(
        {
            "action": "cancel_appointment",
            "customer_name": "김민수",
        },
        {
            "id": "existing-004",
            "customer_name": "김민수",
            "booking_time": "2025-03-17T09:00:00+09:00",
            "customer_type": "재진",
        },
        [],
        now,
    )
    blocked_result = apply_policy(
        {
            "action": "cancel_appointment",
            "customer_name": "김민수",
        },
        {
            "id": "existing-005",
            "customer_name": "김민수",
            "booking_time": "2025-03-17T08:59:00+09:00",
            "customer_type": "재진",
        },
        [],
        now,
    )

    assert allowed_result["allowed"] is True
    assert blocked_result["allowed"] is False
    assert blocked_result["reason"] == POLICY_REASONS["CHANGE_WINDOW_EXPIRED"]


@freeze_time("2025-03-16T09:00:00+09:00")
def test_F019_same_day_new_booking_general_case_requires_confirmation():
    result = apply_policy(
        {
            "action": "book_appointment",
            "booking_time": "2025-03-16T15:00:00+09:00",
            "customer_type": "재진",
        },
        None,
        [],
        datetime.now(timezone.utc),
    )

    assert result["allowed"] is False
    assert result["reason"] == POLICY_REASONS["SAME_DAY_BOOKING_REQUIRES_CONFIRMATION"]
    assert result["recommended_action"] == "clarify"
    assert result["needs_alternative"] is True
    assert all(slot.startswith("2025-03-17") for slot in result["alternative_slots"])


@freeze_time("2025-03-16T09:00:00+09:00")
def test_F019_same_day_new_booking_emergency_case_escalates():
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
    assert result["reason"] == POLICY_REASONS["SAME_DAY_EMERGENCY_ESCALATION"]
    assert result["recommended_action"] == "escalate"
    assert result["needs_alternative"] is False


@freeze_time("2026-04-08T12:00:00Z")
def test_F020_slot_unavailable_returns_alternative_slots():
    appointments = [
        {
            "id": "appt-b",
            "booking_time": "2026-04-10T14:00:00Z",
            "customer_type": "재진",
        }
    ]

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
    assert result["reason"] == POLICY_REASONS["SLOT_UNAVAILABLE"]
    assert result["needs_alternative"] is True
    assert len(result["alternative_slots"]) >= 1
    assert result["alternative_slots"][0] == "2026-04-10T14:30:00Z"
