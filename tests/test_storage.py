import json
from pathlib import Path

import pytest

from src.storage import (
    StorageConflictError,
    StorageDecodeError,
    StorageValidationError,
    StorageWriteError,
    cancel_booking,
    create_booking,
    find_bookings,
    load_bookings,
    resolve_customer_type_from_history,
)


def test_create_booking_persists_required_fields_and_can_be_found_by_contact(tmp_path):
    storage_path = tmp_path / "bookings.json"

    created = create_booking(
        {
            "patient_name": "김민수",
            "patient_contact": "010-1234-5678",
            "birth_date": "1990-02-02",
            "is_proxy_booking": False,
            "department": "내과",
            "date": "2026-03-25",
            "time": "14:00",
            "booking_time": "2026-03-25T14:00:00+09:00",
            "customer_type": "재진",
        },
        path=storage_path,
    )

    results = find_bookings(
        customer_name="다른이름",
        patient_contact="01012345678",
        filters={"birth_date": "1990-02-02"},
        path=storage_path,
    )

    for field_name in [
        "id",
        "patient_name",
        "patient_contact",
        "is_proxy_booking",
        "booking_time",
        "department",
        "customer_type",
        "status",
    ]:
        assert field_name in created

    assert created["patient_name"] == "김민수"
    assert created["customer_name"] == "김민수"
    assert created["patient_contact"] == "010-1234-5678"
    assert created["status"] == "active"
    assert len(results) == 1
    assert results[0]["id"] == created["id"]


def test_create_booking_without_patient_contact_succeeds(tmp_path):
    """배치 모드 티켓에는 patient_contact가 없으므로 저장이 허용되어야 한다."""
    storage_path = tmp_path / "bookings.json"

    created = create_booking(
        {
            "patient_name": "김민수",
            "is_proxy_booking": False,
            "department": "내과",
            "booking_time": "2026-03-25T14:00:00+09:00",
            "customer_type": "재진",
        },
        path=storage_path,
    )
    assert created["patient_name"] == "김민수"
    assert created["status"] == "active"


def test_resolve_customer_type_uses_patient_contact_before_name(tmp_path):
    storage_path = tmp_path / "bookings.json"
    create_booking(
        {
            "patient_name": "김민수",
            "patient_contact": "010-1111-1111",
            "birth_date": "1988-01-01",
            "is_proxy_booking": False,
            "department": "내과",
            "booking_time": "2026-03-25T14:00:00+09:00",
            "customer_type": "재진",
        },
        path=storage_path,
    )
    create_booking(
        {
            "patient_name": "김민수",
            "patient_contact": "010-2222-2222",
            "birth_date": "1990-02-02",
            "is_proxy_booking": True,
            "department": "정형외과",
            "booking_time": "2026-03-26T10:00:00+09:00",
            "customer_type": "재진",
        },
        path=storage_path,
    )

    result = resolve_customer_type_from_history(
        customer_name="없는이름",
        patient_contact="01022222222",
        path=storage_path,
    )

    assert result["ambiguous"] is False
    assert result["customer_type"] == "재진"
    assert len(result["matched_bookings"]) == 1
    assert result["matched_bookings"][0]["patient_contact"] == "010-2222-2222"


def test_resolve_customer_type_requires_birth_date_for_duplicate_names_without_contact(tmp_path):
    storage_path = tmp_path / "bookings.json"
    create_booking(
        {
            "patient_name": "김민수",
            "patient_contact": "010-1111-1111",
            "birth_date": "1988-01-01",
            "is_proxy_booking": False,
            "department": "내과",
            "booking_time": "2026-03-25T14:00:00+09:00",
            "customer_type": "재진",
        },
        path=storage_path,
    )
    create_booking(
        {
            "patient_name": "김민수",
            "patient_contact": "010-2222-2222",
            "birth_date": "1990-02-02",
            "is_proxy_booking": False,
            "department": "정형외과",
            "booking_time": "2026-03-26T10:00:00+09:00",
            "customer_type": "재진",
        },
        path=storage_path,
    )

    ambiguous = resolve_customer_type_from_history("김민수", path=storage_path)
    resolved = resolve_customer_type_from_history("김민수", birth_date="1990-02-02", path=storage_path)

    assert ambiguous["ambiguous"] is True
    assert ambiguous["customer_type"] is None
    assert ambiguous["birth_date_candidates"] == ["1988-01-01", "1990-02-02"]
    assert resolved["ambiguous"] is False
    assert resolved["customer_type"] == "재진"


def test_resolve_customer_type_treats_cancelled_only_contact_history_as_first_visit(tmp_path):
    storage_path = tmp_path / "bookings.json"
    create_booking(
        {
            "patient_name": "박영희",
            "patient_contact": "010-5555-5555",
            "birth_date": "1975-05-05",
            "is_proxy_booking": False,
            "department": "내과",
            "booking_time": "2026-03-25T14:00:00+09:00",
            "customer_type": "재진",
            "status": "cancelled",
        },
        path=storage_path,
    )

    result = resolve_customer_type_from_history(
        customer_name="박영희",
        patient_contact="01055555555",
        path=storage_path,
    )

    assert result["ambiguous"] is False
    assert result["has_cancelled_history"] is True
    assert result["has_non_cancelled_history"] is False
    assert result["customer_type"] == "초진"


def test_cancel_booking_updates_status_and_hides_from_active_results(tmp_path):
    storage_path = tmp_path / "bookings.json"
    created = create_booking(
        {
            "patient_name": "최지훈",
            "patient_contact": "010-7777-7777",
            "is_proxy_booking": True,
            "department": "이비인후과",
            "booking_time": "2026-03-25T16:00:00+09:00",
            "customer_type": "초진",
        },
        path=storage_path,
    )

    assert cancel_booking(created["id"], path=storage_path) is True
    assert find_bookings(patient_contact="01077777777", path=storage_path) == []

    cancelled = find_bookings(
        patient_contact="01077777777",
        path=storage_path,
        include_cancelled=True,
    )
    assert len(cancelled) == 1
    assert cancelled[0]["status"] == "cancelled"


def test_load_bookings_returns_empty_list_for_corrupted_file(tmp_path):
    storage_path = tmp_path / "bookings.json"
    storage_path.write_text("{broken json", encoding="utf-8")
    assert load_bookings(storage_path) == []


def test_load_bookings_returns_empty_list_for_missing_file(tmp_path):
    storage_path = tmp_path / "non_existent_bookings.json"
    assert load_bookings(storage_path) == []


def test_create_booking_raises_write_error_and_preserves_original_file(tmp_path, monkeypatch):
    storage_path = tmp_path / "bookings.json"
    original_data = [
        {
            "id": "booking-001",
            "patient_name": "기존환자",
            "customer_name": "기존환자",
            "patient_contact": "010-0000-0000",
            "is_proxy_booking": False,
            "booking_time": "2026-03-24T10:00:00+09:00",
            "department": "내과",
            "customer_type": "재진",
            "status": "active",
        }
    ]
    storage_path.write_text(json.dumps(original_data, ensure_ascii=False), encoding="utf-8")

    def fail_replace(self, target):
        raise PermissionError("write denied")

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(StorageWriteError):
        create_booking(
            {
                "patient_name": "새환자",
                "patient_contact": "010-9999-9999",
                "is_proxy_booking": False,
                "department": "정형외과",
                "booking_time": "2026-03-25T11:00:00+09:00",
                "customer_type": "초진",
            },
            path=storage_path,
        )

    assert json.loads(storage_path.read_text(encoding="utf-8")) == original_data


def test_create_booking_rechecks_storage_before_persist_and_blocks_duplicate_active_booking(tmp_path):
    storage_path = tmp_path / "bookings.json"
    create_booking(
        {
            "patient_name": "김민수",
            "patient_contact": "010-1111-1111",
            "birth_date": "1990-02-02",
            "is_proxy_booking": False,
            "department": "내과",
            "booking_time": "2026-03-25T14:00:00+09:00",
            "customer_type": "재진",
        },
        path=storage_path,
    )

    with pytest.raises(StorageConflictError):
        create_booking(
            {
                "patient_name": "김민수",
                "patient_contact": "01011111111",
                "birth_date": "1990-02-02",
                "is_proxy_booking": False,
                "department": "내과",
                "booking_time": "2026-03-25T14:00:00+09:00",
                "customer_type": "재진",
            },
            path=storage_path,
        )


def test_create_booking_uses_custom_fresh_recheck_and_preserves_existing_file_on_conflict(tmp_path):
    storage_path = tmp_path / "bookings.json"
    existing = create_booking(
        {
            "patient_name": "박영희",
            "patient_contact": "010-3333-3333",
            "birth_date": "1985-03-03",
            "is_proxy_booking": True,
            "department": "이비인후과",
            "booking_time": "2026-03-26T10:00:00+09:00",
            "customer_type": "초진",
        },
        path=storage_path,
    )

    def deny_final_recheck(new_booking, current_bookings):
        assert new_booking["patient_name"] == "최지훈"
        assert current_bookings[0]["id"] == existing["id"]
        return {"allowed": False, "reason": "해당 시간대는 방금 마감되었습니다."}

    with pytest.raises(StorageConflictError, match="방금 마감되었습니다"):
        create_booking(
            {
                "patient_name": "최지훈",
                "patient_contact": "010-4444-4444",
                "is_proxy_booking": False,
                "department": "이비인후과",
                "booking_time": "2026-03-26T10:30:00+09:00",
                "customer_type": "재진",
            },
            path=storage_path,
            availability_rechecker=deny_final_recheck,
        )

    persisted = load_bookings(storage_path)
    assert len(persisted) == 1
    assert persisted[0]["id"] == existing["id"]
