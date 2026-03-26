"""
src/calcom_client.py — Cal.com API v2 단일 진입점 (Q4)

모든 cal.com HTTP 통신, 버전 헤더, 인증은 이 모듈 내부에 캡슐화된다.
agent.py 외에서 직접 import 금지.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

CALCOM_BASE_URL = "https://api.cal.com/v2"
KST_OFFSET = timedelta(hours=9)
_KST = timezone(KST_OFFSET)

# Department key → env-var name (lazy-read at call time)
_DEPT_ENV_MAP: dict[str, str] = {
    "이비인후과": "CALCOM_ENT_ID",
    "내과": "CALCOM_INTERNAL_ID",
    "정형외과": "CALCOM_ORTHO_ID",
}


# ─────────────────────────────────────────────
# 내부 헬퍼
# ─────────────────────────────────────────────

def _api_key() -> str:
    return os.environ.get("CALCOM_API_KEY", "")


def _event_type_id(department: str) -> str:
    env_name = _DEPT_ENV_MAP.get(department, "")
    if not env_name:
        return ""
    return os.environ.get(env_name, "")


def _make_dummy_email(patient_contact: str) -> str:
    """전화번호에서 더미 이메일 생성 (cal.com attendee.email 필수 필드 충족)."""
    digits = "".join(c for c in (patient_contact or "") if c.isdigit())
    return f"{digits or 'unknown'}@kobimedi.local"


def _kst_to_utc_iso(date: str, time: str) -> str:
    """KST 날짜+시간 문자열 → UTC ISO 8601 (Z suffix) 변환."""
    dt_kst = datetime.fromisoformat(f"{date}T{time}:00").replace(tzinfo=_KST)
    dt_utc = dt_kst.astimezone(timezone.utc)
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _common_headers(api_version: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_api_key()}",
        "cal-api-version": api_version,
        "Content-Type": "application/json",
    }


# ─────────────────────────────────────────────
# 공개 API
# ─────────────────────────────────────────────

def is_calcom_enabled(department: str | None = None) -> bool:
    """
    cal.com 연동 활성 여부를 반환한다.

    - CALCOM_API_KEY 미설정 → False (Graceful Degradation)
    - department 지정 시: 해당 분과의 Event Type ID 미설정 → False
    - 그 외 → True
    """
    if not _api_key():
        return False
    if department is not None:
        if not _event_type_id(department):
            return False
    return True


def get_available_slots(department: str, target_date: str) -> Optional[list[str]]:
    """
    cal.com GET /slots API를 호출해 해당 날짜의 가용 시간(HH:MM, KST) 목록을 반환한다.

    Returns
    -------
    list[str]
        가용 슬롯 목록 (빈 리스트 = 자리 없음)
    None
        API 비활성 또는 네트워크/타임아웃/파싱 오류 → Hard Fail
    """
    if not is_calcom_enabled(department):
        return None

    etype_id = _event_type_id(department)
    if not etype_id:
        return None

    url = f"{CALCOM_BASE_URL}/slots"
    headers = _common_headers("2024-09-04")
    params = {
        "eventTypeId": etype_id,
        "start": f"{target_date}T00:00:00+09:00",
        "end": f"{target_date}T23:59:59+09:00",
    }

    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        # v2 응답 구조: {"status": "success", "data": {"YYYY-MM-DD": [{"start": "..."}, ...]}}
        slots_by_date: dict = data.get("data", {})
        raw_slots: list = slots_by_date.get(target_date, [])

        result: list[str] = []
        for slot in raw_slots:
            raw_time = slot.get("start", "")
            if not raw_time:
                continue
            try:
                dt = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
                result.append(dt.astimezone(_KST).strftime("%H:%M"))
            except (ValueError, AttributeError):
                continue

        return result

    except requests.Timeout:
        logger.error(
            "AGENT_HARD_FAIL: cal.com get_available_slots timeout (dept=%s, date=%s)",
            department, target_date,
        )
        return None
    except requests.RequestException as exc:
        logger.error(
            "AGENT_HARD_FAIL: cal.com get_available_slots request error: %s", exc
        )
        return None
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "AGENT_HARD_FAIL: cal.com get_available_slots unexpected error: %s", exc
        )
        return None


def create_booking(
    department: str,
    date: str,
    time: str,
    patient_name: str,
    patient_contact: str,
    customer_type: str = "new",
) -> Optional[dict | bool]:
    """
    cal.com POST /bookings API를 호출해 예약을 생성한다.

    Returns
    -------
    dict
        성공 시 cal.com 응답 데이터
    False
        409 Conflict → Race Condition (슬롯 선점)
    None
        API 비활성 또는 네트워크/타임아웃 오류 → Hard Fail
    """
    if not is_calcom_enabled(department):
        return None

    etype_id = _event_type_id(department)
    if not etype_id:
        return None

    # KST → UTC 변환
    try:
        start_utc = _kst_to_utc_iso(date, time)
    except (ValueError, TypeError) as exc:
        logger.error("AGENT_HARD_FAIL: cal.com create_booking time conversion error: %s", exc)
        return None

    dummy_email = _make_dummy_email(patient_contact)
    url = f"{CALCOM_BASE_URL}/bookings"
    headers = _common_headers("2024-08-13")
    payload = {
        "eventTypeId": int(etype_id),
        "start": start_utc,
        "attendee": {
            "name": patient_name or "환자",
            "email": dummy_email,
            "timeZone": "Asia/Seoul",
        },
        "metadata": {
            "patient_contact": patient_contact,
            "department": department,
            "customer_type": customer_type,
        },
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)

        if response.status_code == 409:
            logger.warning(
                "cal.com create_booking 409 Conflict (Race Condition): dept=%s date=%s time=%s",
                department, date, time,
            )
            return False  # 슬롯 선점 신호

        # cal.com v2는 슬롯 중복 시 409 대신 400 + "already has booking" 반환
        if response.status_code == 400:
            try:
                err_msg = response.json().get("error", {}).get("message", "")
            except Exception:
                err_msg = ""
            if "already has booking" in err_msg or "not available" in err_msg:
                logger.warning(
                    "cal.com create_booking 400 Slot Conflict: dept=%s date=%s time=%s msg=%s",
                    department, date, time, err_msg,
                )
                return False  # 슬롯 선점 신호 (409와 동일 처리)

        response.raise_for_status()
        data = response.json()
        return data.get("data", data)

    except requests.Timeout:
        logger.error(
            "AGENT_HARD_FAIL: cal.com create_booking timeout (dept=%s, date=%s, time=%s)",
            department, date, time,
        )
        return None
    except requests.RequestException as exc:
        logger.error("AGENT_HARD_FAIL: cal.com create_booking request error: %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.error("AGENT_HARD_FAIL: cal.com create_booking unexpected error: %s", exc)
        return None


def list_bookings() -> Optional[list[dict]]:
    """
    cal.com GET /bookings API를 호출해 전체 예약 목록을 반환한다.

    Returns
    -------
    list[dict]
        예약 목록 (각 항목에 id, uid, title, start, end, status 등 포함)
    None
        API 비활성 또는 네트워크/타임아웃 오류
    """
    if not _api_key():
        return None

    url = f"{CALCOM_BASE_URL}/bookings"
    headers = _common_headers("2024-08-13")
    params = {"status": "upcoming"}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        bookings = data.get("data", data)
        return bookings if isinstance(bookings, list) else []

    except requests.Timeout:
        logger.error("cal.com list_bookings timeout")
        return None
    except requests.RequestException as exc:
        logger.error("cal.com list_bookings request error: %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.error("cal.com list_bookings unexpected error: %s", exc)
        return None


def cancel_booking_remote(booking_uid: str) -> Optional[bool]:
    """
    cal.com DELETE /bookings/{uid}/cancel API를 호출해 원격 예약을 취소한다.

    Returns
    -------
    True
        취소 성공 또는 이미 취소된 상태
    None
        API 비활성 또는 네트워크/타임아웃 오류
    """
    if not _api_key():
        return None
    if not booking_uid:
        return None

    url = f"{CALCOM_BASE_URL}/bookings/{booking_uid}/cancel"
    headers = _common_headers("2024-08-13")

    try:
        response = requests.post(
            url, headers=headers,
            json={"cancellationReason": "cleanup"},
            timeout=10,
        )

        if response.status_code in (200, 204):
            return True

        if response.status_code == 404:
            logger.warning("cal.com cancel_booking 404: uid=%s (이미 삭제됨)", booking_uid)
            return True

        response.raise_for_status()
        return True

    except requests.Timeout:
        logger.error("cal.com cancel_booking timeout: uid=%s", booking_uid)
        return None
    except requests.RequestException as exc:
        logger.error("cal.com cancel_booking request error: %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.error("cal.com cancel_booking unexpected error: %s", exc)
        return None
