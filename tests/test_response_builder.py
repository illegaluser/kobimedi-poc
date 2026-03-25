"""Tests for action-specific phrasing in build_missing_info_question (response_builder.py)."""
import pytest

from src.response_builder import build_missing_info_question


# ---------------------------------------------------------------------------
# is_proxy_booking — action-specific phrasing
# ---------------------------------------------------------------------------

class TestIsProxyBookingQuestion:
    def test_book_appointment_default(self):
        msg = build_missing_info_question(["is_proxy_booking"], action_context="book_appointment")
        assert "예약하시는 분이" in msg
        assert "본인이신가요" in msg

    def test_modify_appointment(self):
        msg = build_missing_info_question(["is_proxy_booking"], action_context="modify_appointment")
        assert "예약 변경" in msg
        assert "본인이신가요" in msg

    def test_cancel_appointment(self):
        msg = build_missing_info_question(["is_proxy_booking"], action_context="cancel_appointment")
        assert "예약 취소" in msg
        assert "본인이신가요" in msg

    def test_check_appointment(self):
        msg = build_missing_info_question(["is_proxy_booking"], action_context="check_appointment")
        assert "예약 확인" in msg
        assert "본인이신가요" in msg

    def test_no_action_context_falls_back_to_book(self):
        msg = build_missing_info_question(["is_proxy_booking"])
        assert "본인이신가요" in msg

    def test_different_actions_produce_different_messages(self):
        book = build_missing_info_question(["is_proxy_booking"], action_context="book_appointment")
        modify = build_missing_info_question(["is_proxy_booking"], action_context="modify_appointment")
        cancel = build_missing_info_question(["is_proxy_booking"], action_context="cancel_appointment")
        check = build_missing_info_question(["is_proxy_booking"], action_context="check_appointment")
        assert len({book, modify, cancel, check}) == 4


# ---------------------------------------------------------------------------
# patient_name + patient_contact both missing → combined request
# ---------------------------------------------------------------------------

class TestPatientNameAndContactCombined:
    def test_book_both_missing_asks_together(self):
        msg = build_missing_info_question(
            ["patient_name", "patient_contact"], action_context="book_appointment"
        )
        assert "성함" in msg
        assert "연락처" in msg
        assert "함께" in msg

    def test_modify_both_missing_asks_together(self):
        msg = build_missing_info_question(
            ["patient_name", "patient_contact"], action_context="modify_appointment"
        )
        assert "예약 변경" in msg
        assert "성함" in msg
        assert "연락처" in msg

    def test_cancel_both_missing_asks_together(self):
        msg = build_missing_info_question(
            ["patient_name", "patient_contact"], action_context="cancel_appointment"
        )
        assert "예약 취소" in msg
        assert "성함" in msg
        assert "연락처" in msg

    def test_check_both_missing_asks_together(self):
        msg = build_missing_info_question(
            ["patient_name", "patient_contact"], action_context="check_appointment"
        )
        assert "예약 확인" in msg
        assert "성함" in msg
        assert "연락처" in msg

    def test_combined_message_includes_example_format(self):
        msg = build_missing_info_question(
            ["patient_name", "patient_contact"], action_context="book_appointment"
        )
        assert "010-" in msg  # example format hint


# ---------------------------------------------------------------------------
# patient_name only missing (contact already provided)
# ---------------------------------------------------------------------------

class TestPatientNameOnlyMissing:
    def test_book_name_only(self):
        msg = build_missing_info_question(["patient_name"], action_context="book_appointment")
        assert "성함" in msg
        assert "연락처" not in msg

    def test_modify_name_only(self):
        msg = build_missing_info_question(["patient_name"], action_context="modify_appointment")
        assert "예약 변경" in msg
        assert "성함" in msg
        assert "연락처" not in msg

    def test_cancel_name_only(self):
        msg = build_missing_info_question(["patient_name"], action_context="cancel_appointment")
        assert "예약 취소" in msg
        assert "성함" in msg

    def test_check_name_only(self):
        msg = build_missing_info_question(["patient_name"], action_context="check_appointment")
        assert "예약 확인" in msg
        assert "성함" in msg


# ---------------------------------------------------------------------------
# patient_contact only missing (name already provided)
# ---------------------------------------------------------------------------

class TestPatientContactOnlyMissing:
    def test_book_contact_only(self):
        msg = build_missing_info_question(["patient_contact"], action_context="book_appointment")
        assert "연락처" in msg

    def test_modify_contact_only(self):
        msg = build_missing_info_question(["patient_contact"], action_context="modify_appointment")
        assert "예약 변경" in msg
        assert "연락처" in msg

    def test_cancel_contact_only(self):
        msg = build_missing_info_question(["patient_contact"], action_context="cancel_appointment")
        assert "예약 취소" in msg
        assert "연락처" in msg

    def test_check_contact_only(self):
        msg = build_missing_info_question(["patient_contact"], action_context="check_appointment")
        assert "예약 확인" in msg
        assert "연락처" in msg


# ---------------------------------------------------------------------------
# Other fields are unaffected by the changes
# ---------------------------------------------------------------------------

class TestOtherFieldsUnchanged:
    def test_birth_date(self):
        msg = build_missing_info_question(["birth_date"])
        assert "생년월일" in msg

    def test_department(self):
        msg = build_missing_info_question(["department"])
        assert "분과" in msg

    def test_date(self):
        msg = build_missing_info_question(["date"])
        assert "날짜" in msg

    def test_time(self):
        msg = build_missing_info_question(["time"])
        assert "시" in msg

    def test_slot_selection(self):
        msg = build_missing_info_question(["slot_selection"])
        assert "번호" in msg

    def test_appointment_target_cancel(self):
        msg = build_missing_info_question(
            ["appointment_target"], action_context="cancel_appointment"
        )
        assert "취소" in msg

    def test_appointment_target_modify(self):
        msg = build_missing_info_question(
            ["appointment_target"], action_context="modify_appointment"
        )
        assert "변경" in msg

    def test_empty_fields_returns_fallback(self):
        msg = build_missing_info_question([])
        assert isinstance(msg, str)
        assert len(msg) > 0
