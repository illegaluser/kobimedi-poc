"""
tests/test_calcom.py — cal.com 연동 단위 테스트 (Q4)

모든 HTTP 호출은 unittest.mock.patch로 모킹하여
실제 네트워크 통신이 발생하지 않는다.
"""

from __future__ import annotations

import json
import os
from datetime import timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest
import requests

from src import calcom_client


# ─────────────────────────────────────────────────────────────
# 픽스처 / 헬퍼
# ─────────────────────────────────────────────────────────────

_KST = timezone(timedelta(hours=9))

ENV_WITH_KEY = {
    "CALCOM_API_KEY": "test-api-key",
    "CALCOM_ENT_ID": "111",
    "CALCOM_INTERNAL_ID": "222",
    "CALCOM_ORTHO_ID": "333",
}


def _mock_response(status_code: int, body: dict) -> MagicMock:
    """requests.Response를 흉내 낸 Mock 객체 생성."""
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = body
    if status_code >= 400:
        mock.raise_for_status.side_effect = requests.HTTPError(
            response=mock
        )
    else:
        mock.raise_for_status.return_value = None
    return mock


# ─────────────────────────────────────────────────────────────
# is_calcom_enabled 테스트
# ─────────────────────────────────────────────────────────────

class TestIsCalcomEnabled:
    def test_disabled_without_api_key(self):
        """CALCOM_API_KEY 미설정 → False."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove key if present
            env = {k: v for k, v in os.environ.items() if k != "CALCOM_API_KEY"}
            with patch.dict(os.environ, env, clear=True):
                assert calcom_client.is_calcom_enabled() is False

    def test_enabled_with_api_key_only(self):
        """CALCOM_API_KEY만 있고 department 미지정 → True."""
        with patch.dict(os.environ, {"CALCOM_API_KEY": "key123"}, clear=False):
            assert calcom_client.is_calcom_enabled() is True

    def test_disabled_missing_dept_event_id(self):
        """API Key 있지만 해당 분과 Event ID 없음 → False (동선 4.2)."""
        env = {"CALCOM_API_KEY": "key123"}
        # 치과 → 매핑 없음
        with patch.dict(os.environ, env, clear=False):
            # Remove ENT_ID etc if set
            for k in ["CALCOM_ENT_ID", "CALCOM_INTERNAL_ID", "CALCOM_ORTHO_ID"]:
                os.environ.pop(k, None)
            assert calcom_client.is_calcom_enabled("이비인후과") is False

    def test_enabled_with_all_env_vars(self):
        """API Key + 분과 Event ID 모두 설정 → True."""
        with patch.dict(os.environ, ENV_WITH_KEY, clear=False):
            assert calcom_client.is_calcom_enabled("이비인후과") is True
            assert calcom_client.is_calcom_enabled("내과") is True
            assert calcom_client.is_calcom_enabled("정형외과") is True

    def test_disabled_unsupported_department(self):
        """지원하지 않는 분과 (치과 등) → Event ID 없음 → False."""
        with patch.dict(os.environ, ENV_WITH_KEY, clear=False):
            assert calcom_client.is_calcom_enabled("치과") is False

    def test_empty_api_key_is_disabled(self):
        """빈 문자열 API Key → False."""
        with patch.dict(os.environ, {"CALCOM_API_KEY": ""}, clear=False):
            assert calcom_client.is_calcom_enabled() is False


# ─────────────────────────────────────────────────────────────
# _make_dummy_email 테스트
# ─────────────────────────────────────────────────────────────

class TestMakeDummyEmail:
    def test_phone_to_email(self):
        """010-1234-5678 → 01012345678@kobimedi.local."""
        email = calcom_client._make_dummy_email("010-1234-5678")
        assert email == "01012345678@kobimedi.local"

    def test_unformatted_phone(self):
        """01012345678 → 01012345678@kobimedi.local."""
        email = calcom_client._make_dummy_email("01012345678")
        assert email == "01012345678@kobimedi.local"

    def test_empty_contact(self):
        """빈 문자열 → unknown@kobimedi.local."""
        email = calcom_client._make_dummy_email("")
        assert email == "unknown@kobimedi.local"

    def test_none_contact(self):
        """None → unknown@kobimedi.local."""
        email = calcom_client._make_dummy_email(None)
        assert email == "unknown@kobimedi.local"


# ─────────────────────────────────────────────────────────────
# _kst_to_utc_iso 테스트
# ─────────────────────────────────────────────────────────────

class TestKstToUtcIso:
    def test_conversion(self):
        """KST 10:00 → UTC 01:00 (Z suffix)."""
        result = calcom_client._kst_to_utc_iso("2026-04-01", "10:00")
        assert result == "2026-04-01T01:00:00.000Z"

    def test_midnight_kst(self):
        """KST 00:00 = 전날 UTC 15:00."""
        result = calcom_client._kst_to_utc_iso("2026-04-01", "00:00")
        assert result == "2026-03-31T15:00:00.000Z"

    def test_end_of_day_kst(self):
        """KST 18:00 → UTC 09:00."""
        result = calcom_client._kst_to_utc_iso("2026-04-01", "18:00")
        assert result == "2026-04-01T09:00:00.000Z"


# ─────────────────────────────────────────────────────────────
# get_available_slots 테스트
# ─────────────────────────────────────────────────────────────

class TestGetAvailableSlots:
    """동선 1.3, 3.1, 4.1, 4.2 커버."""

    def test_disabled_returns_none(self):
        """CALCOM_API_KEY 없음 → None 반환 (동선 4.1)."""
        with patch.dict(os.environ, {}, clear=True):
            with patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != "CALCOM_API_KEY"}, clear=True):
                result = calcom_client.get_available_slots("이비인후과", "2026-04-01")
                assert result is None

    def test_unmapped_dept_returns_none(self):
        """매핑 안된 분과 → None 반환 (동선 4.2)."""
        with patch.dict(os.environ, {"CALCOM_API_KEY": "key"}, clear=False):
            for k in ["CALCOM_ENT_ID", "CALCOM_INTERNAL_ID", "CALCOM_ORTHO_ID"]:
                os.environ.pop(k, None)
            result = calcom_client.get_available_slots("치과", "2026-04-01")
            assert result is None

    def test_success_returns_hhmm_list(self):
        """정상 응답 → HH:MM 리스트 반환 (동선 1.3)."""
        api_body = {
            "status": "success",
            "data": {
                "2026-04-01": [
                    {"start": "2026-04-01T01:00:00.000Z"},  # KST 10:00
                    {"start": "2026-04-01T01:30:00.000Z"},  # KST 10:30
                    {"start": "2026-04-01T02:00:00.000Z"},  # KST 11:00
                ]
            },
        }
        with patch.dict(os.environ, ENV_WITH_KEY, clear=False):
            with patch("requests.get", return_value=_mock_response(200, api_body)):
                result = calcom_client.get_available_slots("이비인후과", "2026-04-01")
        assert result == ["10:00", "10:30", "11:00"]

    def test_empty_slots_returns_empty_list(self):
        """가용 슬롯 없음 → 빈 리스트 반환."""
        api_body = {
            "status": "success",
            "data": {"2026-04-01": []},
        }
        with patch.dict(os.environ, ENV_WITH_KEY, clear=False):
            with patch("requests.get", return_value=_mock_response(200, api_body)):
                result = calcom_client.get_available_slots("이비인후과", "2026-04-01")
        assert result == []

    def test_timeout_returns_none(self):
        """타임아웃 → None 반환 + 거짓 성공 없음 (동선 3.1)."""
        with patch.dict(os.environ, ENV_WITH_KEY, clear=False):
            with patch("requests.get", side_effect=requests.Timeout("timeout")):
                result = calcom_client.get_available_slots("이비인후과", "2026-04-01")
        assert result is None

    def test_network_error_returns_none(self):
        """네트워크 오류 → None 반환 (동선 3.1)."""
        with patch.dict(os.environ, ENV_WITH_KEY, clear=False):
            with patch(
                "requests.get",
                side_effect=requests.ConnectionError("connection refused"),
            ):
                result = calcom_client.get_available_slots("이비인후과", "2026-04-01")
        assert result is None

    def test_500_error_returns_none(self):
        """500 서버 에러 → None 반환."""
        with patch.dict(os.environ, ENV_WITH_KEY, clear=False):
            with patch("requests.get", return_value=_mock_response(500, {})):
                result = calcom_client.get_available_slots("이비인후과", "2026-04-01")
        assert result is None

    def test_correct_headers_sent(self):
        """cal-api-version: 2024-09-04 헤더 검증."""
        api_body = {"status": "success", "data": {}}
        with patch.dict(os.environ, ENV_WITH_KEY, clear=False):
            with patch("requests.get", return_value=_mock_response(200, api_body)) as mock_get:
                calcom_client.get_available_slots("내과", "2026-04-01")
                call_kwargs = mock_get.call_args
                headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
                assert headers.get("cal-api-version") == "2024-09-04"
                assert "Bearer test-api-key" in headers.get("Authorization", "")

    def test_date_mismatch_returns_empty(self):
        """응답에 다른 날짜만 있는 경우 → 빈 리스트."""
        api_body = {
            "status": "success",
            "data": {
                "2026-04-02": [{"start": "2026-04-02T01:00:00.000Z"}]
            },
        }
        with patch.dict(os.environ, ENV_WITH_KEY, clear=False):
            with patch("requests.get", return_value=_mock_response(200, api_body)):
                result = calcom_client.get_available_slots("이비인후과", "2026-04-01")
        assert result == []


# ─────────────────────────────────────────────────────────────
# create_booking 테스트
# ─────────────────────────────────────────────────────────────

class TestCreateBooking:
    """동선 1.1, 2.2, 3.2, 4.1, 4.2 커버."""

    def test_disabled_returns_none(self):
        """CALCOM_API_KEY 없음 → None 반환 (동선 4.1)."""
        with patch.dict(os.environ, {}, clear=True):
            with patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != "CALCOM_API_KEY"}, clear=True):
                result = calcom_client.create_booking(
                    "이비인후과", "2026-04-01", "10:00", "홍길동", "010-1234-5678"
                )
                assert result is None

    def test_success_returns_dict(self):
        """예약 생성 성공 → cal.com 응답 dict 반환 (동선 1.1)."""
        api_body = {
            "status": "success",
            "data": {
                "id": 999,
                "status": "accepted",
                "attendees": [{"email": "01012345678@kobimedi.local"}],
            },
        }
        with patch.dict(os.environ, ENV_WITH_KEY, clear=False):
            with patch("requests.post", return_value=_mock_response(200, api_body)):
                result = calcom_client.create_booking(
                    "이비인후과", "2026-04-01", "10:00", "홍길동", "010-1234-5678"
                )
        assert isinstance(result, dict)
        assert result.get("id") == 999

    def test_conflict_409_returns_false(self):
        """409 Conflict → False 반환 (Race Condition, 동선 2.2)."""
        with patch.dict(os.environ, ENV_WITH_KEY, clear=False):
            with patch("requests.post", return_value=_mock_response(409, {"error": "conflict"})):
                result = calcom_client.create_booking(
                    "이비인후과", "2026-04-01", "10:00", "홍길동", "010-1234-5678"
                )
        assert result is False

    def test_timeout_returns_none(self):
        """타임아웃 → None 반환 + 거짓 성공 없음 (동선 3.2)."""
        with patch.dict(os.environ, ENV_WITH_KEY, clear=False):
            with patch("requests.post", side_effect=requests.Timeout("timeout")):
                result = calcom_client.create_booking(
                    "이비인후과", "2026-04-01", "10:00", "홍길동", "010-1234-5678"
                )
        assert result is None

    def test_network_error_returns_none(self):
        """네트워크 오류 → None 반환."""
        with patch.dict(os.environ, ENV_WITH_KEY, clear=False):
            with patch(
                "requests.post",
                side_effect=requests.ConnectionError("unreachable"),
            ):
                result = calcom_client.create_booking(
                    "이비인후과", "2026-04-01", "10:00", "홍길동", "010-1234-5678"
                )
        assert result is None

    def test_500_error_returns_none(self):
        """500 에러 → None 반환."""
        with patch.dict(os.environ, ENV_WITH_KEY, clear=False):
            with patch("requests.post", return_value=_mock_response(500, {})):
                result = calcom_client.create_booking(
                    "이비인후과", "2026-04-01", "10:00", "홍길동", "010-1234-5678"
                )
        assert result is None

    def test_correct_headers_sent(self):
        """cal-api-version: 2024-08-13 헤더 검증."""
        api_body = {"status": "success", "data": {"id": 1}}
        with patch.dict(os.environ, ENV_WITH_KEY, clear=False):
            with patch("requests.post", return_value=_mock_response(200, api_body)) as mock_post:
                calcom_client.create_booking(
                    "이비인후과", "2026-04-01", "10:00", "홍길동", "010-1234-5678"
                )
                call_kwargs = mock_post.call_args
                headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
                assert headers.get("cal-api-version") == "2024-08-13"
                assert "Bearer test-api-key" in headers.get("Authorization", "")

    def test_payload_structure(self):
        """전송 payload에 더미 이메일, attendee, start(UTC), notes 포함 검증."""
        api_body = {"status": "success", "data": {"id": 42}}
        with patch.dict(os.environ, ENV_WITH_KEY, clear=False):
            with patch("requests.post", return_value=_mock_response(200, api_body)) as mock_post:
                calcom_client.create_booking(
                    "이비인후과", "2026-04-01", "10:00", "홍길동", "010-1234-5678"
                )
                call_kwargs = mock_post.call_args
                payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json", {})

        # eventTypeId → int
        assert payload["eventTypeId"] == 111
        # start → UTC ISO 8601
        assert payload["start"] == "2026-04-01T01:00:00.000Z"
        # attendee
        attendee = payload["attendee"]
        assert attendee["email"] == "01012345678@kobimedi.local"
        assert attendee["name"] == "홍길동"
        assert attendee["timeZone"] == "Asia/Seoul"
        # metadata (notes 필드는 cal.com v2에서 미지원 → 제거됨)
        assert payload["metadata"]["patient_contact"] == "010-1234-5678"

    def test_unmapped_dept_returns_none(self):
        """매핑 안된 분과 → None 반환 (동선 4.2)."""
        with patch.dict(os.environ, {"CALCOM_API_KEY": "key"}, clear=False):
            for k in ["CALCOM_ENT_ID", "CALCOM_INTERNAL_ID", "CALCOM_ORTHO_ID"]:
                os.environ.pop(k, None)
            result = calcom_client.create_booking(
                "치과", "2026-04-01", "10:00", "홍길동", "010-1234-5678"
            )
        assert result is None

    def test_no_false_success_on_any_error(self):
        """어떤 예외도 True/예약완료 결과를 반환하지 않는다."""
        errors = [
            requests.Timeout(),
            requests.ConnectionError(),
            requests.HTTPError(),
        ]
        with patch.dict(os.environ, ENV_WITH_KEY, clear=False):
            for err in errors:
                with patch("requests.post", side_effect=err):
                    result = calcom_client.create_booking(
                        "이비인후과", "2026-04-01", "10:00", "홍길동", "010-1234-5678"
                    )
                    # None 또는 False만 허용 — dict(성공) 절대 불가
                    assert result is None or result is False, (
                        f"거짓 성공: {err.__class__.__name__} 발생 시 result={result}"
                    )


# ─────────────────────────────────────────────────────────────
# agent.py 통합 경로 테스트 (Position 3 Race Condition)
# ─────────────────────────────────────────────────────────────

class TestAgentCalcomIntegration:
    """agent.py의 cal.com 통합 경로를 공개 API 모킹으로 검증."""

    @pytest.fixture(autouse=True)
    def _patch_env(self):
        """모든 테스트에서 cal.com 활성 환경 설정."""
        with patch.dict(os.environ, ENV_WITH_KEY, clear=False):
            yield

    def _make_pending_confirmation_state(self, department="이비인후과"):
        """pending_confirmation이 설정된 세션 상태 반환."""
        return {
            "conversation_history": [],
            "accumulated_slots": {"date": "2026-04-01", "time": "10:00", "department": department},
            "customer_name": "홍길동",
            "patient_name": "홍길동",
            "patient_contact": "010-1234-5678",
            "birth_date": None,
            "is_proxy_booking": False,
            "resolved_customer_type": "new",
            "pending_confirmation": {
                "action": "book_appointment",
                "appointment": {
                    "customer_name": "홍길동",
                    "patient_name": "홍길동",
                    "patient_contact": "010-1234-5678",
                    "is_proxy_booking": False,
                    "birth_date": None,
                    "department": department,
                    "date": "2026-04-01",
                    "time": "10:00",
                    "booking_time": "2026-04-01T10:00:00+09:00",
                    "customer_type": "new",
                },
            },
            "pending_action": "book_appointment",
            "pending_missing_info": [],
            "pending_missing_info_queue": [],
            "pending_candidates": None,
            "pending_alternative_slots": None,
            "clarify_turn_count": 0,
            "last_result": None,
        }

    def test_race_condition_conflict_returns_clarify(self):
        """Race Condition (409) 시 clarify 반환, 로컬 DB 저장 안 됨 (동선 2.2)."""
        from src.agent import _handle_pending_confirmation
        from datetime import datetime, timezone

        state = self._make_pending_confirmation_state()
        all_appointments = []

        with patch.object(calcom_client, "create_booking", return_value=False):
            with patch("src.agent.create_booking") as mock_local_create:
                result = _handle_pending_confirmation(
                    "네",
                    state,
                    all_appointments,
                    datetime.now(timezone.utc),
                    ticket={},
                    customer_type="new",
                )

        assert result is not None
        assert result["action"] == "clarify"
        assert "마감" in result["response"]
        # 로컬 DB 저장이 호출되지 않아야 함
        mock_local_create.assert_not_called()

    def test_calcom_timeout_returns_clarify_no_local_save(self):
        """cal.com 타임아웃 시 clarify 반환, 로컬 DB 저장 없음 (동선 3.2)."""
        from src.agent import _handle_pending_confirmation
        from datetime import datetime, timezone

        state = self._make_pending_confirmation_state()
        all_appointments = []

        with patch.object(calcom_client, "create_booking", return_value=None):
            with patch("src.agent.create_booking") as mock_local_create:
                result = _handle_pending_confirmation(
                    "네",
                    state,
                    all_appointments,
                    datetime.now(timezone.utc),
                    ticket={},
                    customer_type="new",
                )

        assert result is not None
        assert result["action"] == "clarify"
        assert "처리가 불가" in result["response"] or "응답 지연" in result["response"]
        mock_local_create.assert_not_called()

    def test_calcom_success_proceeds_to_local_save(self):
        """cal.com 성공 시 로컬 DB 저장도 정상 진행 (동선 1.1)."""
        from src.agent import _handle_pending_confirmation
        from datetime import datetime, timezone

        state = self._make_pending_confirmation_state()
        all_appointments = []

        fake_calcom_result = {"id": 999, "status": "accepted"}
        fake_local_booking = {
            "id": "local-001",
            "customer_name": "홍길동",
            "department": "이비인후과",
            "date": "2026-04-01",
            "time": "10:00",
            "booking_time": "2026-04-01T10:00:00+09:00",
        }

        with patch.object(calcom_client, "create_booking", return_value=fake_calcom_result):
            with patch("src.agent.create_booking", return_value=fake_local_booking):
                result = _handle_pending_confirmation(
                    "네",
                    state,
                    all_appointments,
                    datetime.now(timezone.utc),
                    ticket={},
                    customer_type="new",
                )

        assert result is not None
        assert result["action"] == "book_appointment"

    def test_calcom_disabled_proceeds_normally(self):
        """cal.com 비활성 시 기존 로컬 예약 정상 처리 (동선 4.1)."""
        from src.agent import _handle_pending_confirmation
        from datetime import datetime, timezone

        state = self._make_pending_confirmation_state()
        all_appointments = []

        fake_local_booking = {
            "id": "local-002",
            "customer_name": "홍길동",
            "department": "이비인후과",
            "date": "2026-04-01",
            "time": "10:00",
            "booking_time": "2026-04-01T10:00:00+09:00",
        }

        # CALCOM_API_KEY 없는 환경 (Graceful Degradation)
        with patch.dict(os.environ, {}, clear=True):
            env_without_key = {k: v for k, v in os.environ.items() if k != "CALCOM_API_KEY"}
            with patch.dict(os.environ, env_without_key, clear=True):
                with patch("src.agent.create_booking", return_value=fake_local_booking):
                    result = _handle_pending_confirmation(
                        "네",
                        state,
                        all_appointments,
                        datetime.now(timezone.utc),
                        ticket={},
                        customer_type="new",
                    )

        assert result is not None
        assert result["action"] == "book_appointment"


# ─────────────────────────────────────────────────────────────
# 배치 모드 통합 경로 테스트 (동선 5.1, 5.2)
# ─────────────────────────────────────────────────────────────

class TestBatchModeCalcom:
    """배치 모드에서의 cal.com 즉시 Drop 및 즉시 확정 경로 검증."""

    @pytest.fixture(autouse=True)
    def _patch_env_enabled(self):
        with patch.dict(os.environ, ENV_WITH_KEY, clear=False):
            yield

    def _base_ticket(self, time="10:00"):
        return {
            "message": "내과 예약 부탁드립니다",
            "customer_name": "김영희",
            "customer_type": "new",
            "patient_name": "김영희",
            "patient_contact": "010-9999-8888",
            "is_proxy_booking": False,
            "context": {
                "preferred_department": "내과",
                "preferred_date": "2026-04-01",
                "preferred_time": time,
            },
        }

    def test_batch_slot_taken_returns_clarify_with_alternatives(self):
        """배치 모드 슬롯 마감 → clarify + 대안 포함 (동선 5.1)."""
        from src.agent import process_ticket
        from datetime import datetime, timezone
        from unittest.mock import MagicMock

        # 사용자 요청 시간은 10:00이지만 cal.com에는 10:00 없음
        mock_slots = ["10:30", "11:00"]

        with patch.object(calcom_client, "get_available_slots", return_value=mock_slots):
            with patch("src.agent.classify_intent") as mock_classify:
                mock_classify.return_value = {
                    "action": "book_appointment",
                    "department": "내과",
                    "date": "2026-04-01",
                    "time": "10:00",
                    "customer_type": "new",
                    "missing_info": [],
                }
                with patch("src.agent.apply_policy") as mock_policy:
                    from src.models import Action
                    policy_mock = MagicMock()
                    policy_mock.action.value = "book_appointment"
                    policy_mock.suggested_slots = []
                    mock_policy.return_value = policy_mock

                    result = process_ticket(
                        ticket=self._base_ticket("10:00"),
                        all_appointments=[],
                        session_state=None,  # 배치 모드
                        now=datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc),
                    )

        assert result["action"] == "clarify"
        assert "마감" in result["response"] or "10:30" in result["response"] or "대안" in result["response"]

    def test_batch_slot_available_returns_book_appointment(self):
        """배치 모드 슬롯 가용 → 즉시 book_appointment 반환 (동선 5.2)."""
        from src.agent import process_ticket
        from datetime import datetime, timezone
        from unittest.mock import MagicMock

        mock_slots = ["10:00", "10:30", "11:00"]  # 요청 시간 10:00 포함
        mock_booking = {**self._base_ticket("10:00"), "id": "b-test", "status": "active", "booking_time": "2026-04-01T10:00:00+09:00"}

        with patch.object(calcom_client, "get_available_slots", return_value=mock_slots):
            with patch.object(calcom_client, "create_booking", return_value={"id": 777}):
                with patch("src.agent.create_booking", return_value=mock_booking):
                    with patch("src.agent.classify_intent") as mock_classify:
                        mock_classify.return_value = {
                            "action": "book_appointment",
                            "department": "내과",
                            "date": "2026-04-01",
                            "time": "10:00",
                            "customer_type": "new",
                            "missing_info": [],
                        }
                        with patch("src.agent.apply_policy") as mock_policy:
                            policy_mock = MagicMock()
                            policy_mock.action.value = "book_appointment"
                            policy_mock.suggested_slots = []
                            mock_policy.return_value = policy_mock

                            result = process_ticket(
                                ticket=self._base_ticket("10:00"),
                                all_appointments=[],
                                session_state=None,
                                now=datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc),
                            )

        assert result["action"] == "book_appointment"

    def test_batch_calcom_disabled_still_books(self):
        """배치 모드에서 cal.com 비활성 시 기존 로직 정상 동작 (동선 4.1)."""
        from src.agent import process_ticket
        from datetime import datetime, timezone
        from unittest.mock import MagicMock

        mock_booking = {**self._base_ticket("10:00"), "id": "b-test", "status": "active", "booking_time": "2026-04-01T10:00:00+09:00"}

        with patch.dict(os.environ, {}, clear=True):
            env_without_key = {k: v for k, v in os.environ.items() if k != "CALCOM_API_KEY"}
            with patch.dict(os.environ, env_without_key, clear=True):
                with patch("src.agent.create_booking", return_value=mock_booking):
                    with patch("src.agent.classify_intent") as mock_classify:
                        mock_classify.return_value = {
                            "action": "book_appointment",
                            "department": "내과",
                            "date": "2026-04-01",
                            "time": "10:00",
                            "customer_type": "new",
                            "missing_info": [],
                        }
                        with patch("src.agent.apply_policy") as mock_policy:
                            policy_mock = MagicMock()
                            policy_mock.action.value = "book_appointment"
                            policy_mock.suggested_slots = []
                            mock_policy.return_value = policy_mock

                            result = process_ticket(
                                ticket=self._base_ticket("10:00"),
                                all_appointments=[],
                                session_state=None,
                                now=datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc),
                            )

        assert result["action"] == "book_appointment"


# ─────────────────────────────────────────────────────────────
# list_bookings 테스트
# ─────────────────────────────────────────────────────────────

class TestListBookings:
    """list_bookings() 단위 테스트."""

    def test_disabled_returns_none(self):
        """API 키 미설정 → None."""
        with patch.dict(os.environ, {}, clear=True):
            env = {k: v for k, v in os.environ.items() if k != "CALCOM_API_KEY"}
            with patch.dict(os.environ, env, clear=True):
                assert calcom_client.list_bookings() is None

    def test_success_returns_list(self):
        """정상 응답 → list 반환."""
        body = {"status": "success", "data": [
            {"uid": "abc-123", "title": "내과", "start": "2026-04-01T10:00:00Z"},
            {"uid": "def-456", "title": "이비인후과", "start": "2026-04-01T11:00:00Z"},
        ]}
        with patch.dict(os.environ, ENV_WITH_KEY, clear=False):
            with patch("requests.get", return_value=_mock_response(200, body)):
                result = calcom_client.list_bookings()
        assert isinstance(result, list)
        assert len(result) == 2

    def test_empty_returns_empty_list(self):
        """예약 없음 → 빈 리스트."""
        body = {"status": "success", "data": []}
        with patch.dict(os.environ, ENV_WITH_KEY, clear=False):
            with patch("requests.get", return_value=_mock_response(200, body)):
                result = calcom_client.list_bookings()
        assert result == []

    def test_timeout_returns_none(self):
        """타임아웃 → None."""
        with patch.dict(os.environ, ENV_WITH_KEY, clear=False):
            with patch("requests.get", side_effect=requests.Timeout("timeout")):
                assert calcom_client.list_bookings() is None

    def test_500_returns_none(self):
        """서버 오류 → None."""
        with patch.dict(os.environ, ENV_WITH_KEY, clear=False):
            with patch("requests.get", return_value=_mock_response(500, {"error": "server"})):
                assert calcom_client.list_bookings() is None


# ─────────────────────────────────────────────────────────────
# cancel_booking_remote 테스트
# ─────────────────────────────────────────────────────────────

class TestCancelBookingRemote:
    """cancel_booking_remote() 단위 테스트."""

    def test_disabled_returns_none(self):
        """API 키 미설정 → None."""
        with patch.dict(os.environ, {}, clear=True):
            env = {k: v for k, v in os.environ.items() if k != "CALCOM_API_KEY"}
            with patch.dict(os.environ, env, clear=True):
                assert calcom_client.cancel_booking_remote("abc-123") is None

    def test_empty_uid_returns_none(self):
        """빈 uid → None."""
        with patch.dict(os.environ, ENV_WITH_KEY, clear=False):
            assert calcom_client.cancel_booking_remote("") is None

    def test_success_200_returns_true(self):
        """200 성공 → True."""
        with patch.dict(os.environ, ENV_WITH_KEY, clear=False):
            with patch("requests.post", return_value=_mock_response(200, {"status": "success"})):
                assert calcom_client.cancel_booking_remote("abc-123") is True

    def test_success_204_returns_true(self):
        """204 No Content → True."""
        mock = MagicMock()
        mock.status_code = 204
        mock.raise_for_status.return_value = None
        with patch.dict(os.environ, ENV_WITH_KEY, clear=False):
            with patch("requests.post", return_value=mock):
                assert calcom_client.cancel_booking_remote("abc-123") is True

    def test_404_returns_true(self):
        """404 Not Found (이미 삭제됨) → True."""
        mock = _mock_response(404, {"error": "not found"})
        with patch.dict(os.environ, ENV_WITH_KEY, clear=False):
            with patch("requests.post", return_value=mock):
                assert calcom_client.cancel_booking_remote("abc-123") is True

    def test_timeout_returns_none(self):
        """타임아웃 → None."""
        with patch.dict(os.environ, ENV_WITH_KEY, clear=False):
            with patch("requests.post", side_effect=requests.Timeout("timeout")):
                assert calcom_client.cancel_booking_remote("abc-123") is None

    def test_500_returns_none(self):
        """서버 오류 → None."""
        with patch.dict(os.environ, ENV_WITH_KEY, clear=False):
            with patch("requests.post", return_value=_mock_response(500, {"error": "server"})):
                assert calcom_client.cancel_booking_remote("abc-123") is None
