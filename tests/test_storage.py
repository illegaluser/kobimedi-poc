from src.storage import create_booking, find_bookings, resolve_customer_type_from_history


def test_create_booking_persists_birth_date_and_can_be_found(tmp_path):
    storage_path = tmp_path / "bookings.json"

    created = create_booking(
        {
            "customer_name": "김민수",
            "birth_date": "1990-02-02",
            "department": "내과",
            "date": "2026-03-25",
            "time": "14:00",
            "booking_time": "2026-03-25T14:00:00+09:00",
            "customer_type": "재진",
        },
        path=storage_path,
    )

    results = find_bookings(
        customer_name="김민수",
        filters={"birth_date": "1990-02-02"},
        path=storage_path,
    )

    assert created["birth_date"] == "1990-02-02"
    assert len(results) == 1
    assert results[0]["id"] == created["id"]


def test_resolve_customer_type_requires_birth_date_for_duplicate_names(tmp_path):
    storage_path = tmp_path / "bookings.json"
    create_booking(
        {
            "customer_name": "김민수",
            "birth_date": "1988-01-01",
            "department": "내과",
            "date": "2026-03-25",
            "time": "14:00",
            "booking_time": "2026-03-25T14:00:00+09:00",
            "customer_type": "재진",
        },
        path=storage_path,
    )
    create_booking(
        {
            "customer_name": "김민수",
            "birth_date": "1990-02-02",
            "department": "정형외과",
            "date": "2026-03-26",
            "time": "10:00",
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


def test_resolve_customer_type_treats_cancelled_only_history_as_first_visit(tmp_path):
    storage_path = tmp_path / "bookings.json"
    create_booking(
        {
            "customer_name": "박영희",
            "birth_date": "1975-05-05",
            "department": "내과",
            "date": "2026-03-25",
            "time": "14:00",
            "booking_time": "2026-03-25T14:00:00+09:00",
            "customer_type": "재진",
            "status": "cancelled",
        },
        path=storage_path,
    )

    result = resolve_customer_type_from_history("박영희", birth_date="1975-05-05", path=storage_path)

    assert result["ambiguous"] is False
    assert result["has_cancelled_history"] is True
    assert result["has_non_cancelled_history"] is False
    assert result["customer_type"] == "초진"
