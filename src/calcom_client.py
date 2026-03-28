"""
src/calcom_client.py — Cal.com API v2 단일 진입점 (Q4)

이 모듈은 Cal.com v2 REST API와의 모든 HTTP 통신을 캡슐화하는 유일한 진입점이다.
인증(Bearer Token), API 버전 헤더, 요청/응답 처리가 모두 이 파일 안에서 이루어지며,
외부 모듈(agent.py)은 이 모듈의 공개 함수만 호출한다.

■ Graceful Degradation 전략
  - 환경변수 CALCOM_API_KEY가 설정되어 있지 않으면 모든 함수가 크래시 없이
    None 또는 False를 반환한다. 이를 통해 Cal.com 미연동 환경에서도 챗봇이
    정상 동작할 수 있다.

■ 반환값 규약 (모든 공개 함수에 적용)
  - dict  → 성공 (Cal.com 응답 데이터)
  - False → 409/400 Conflict (슬롯 선점 등 Race Condition)
  - None  → Hard Fail (네트워크 오류, 타임아웃, 파싱 실패 등)
  ※ agent.py는 None을 받으면 AGENT_HARD_FAIL을 사용자에게 반환한다.

■ 진료과(department) → Cal.com Event Type ID 매핑
  - 각 진료과는 환경변수(CALCOM_ENT_ID 등)를 통해 Cal.com의
    Event Type ID에 매핑된다. _DEPT_ENV_MAP 딕셔너리가 이 매핑을 관리한다.

■ 접근 제한
  - agent.py 외에서 직접 import 금지.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Cal.com v2 API 기본 URL. 모든 요청이 이 주소를 기반으로 구성된다.
CALCOM_BASE_URL = "https://api.cal.com/v2"

# 한국 표준시(KST)는 UTC+9이다. Cal.com API는 UTC를 사용하므로
# 사용자 입력(KST)을 UTC로 변환할 때 이 오프셋을 사용한다.
KST_OFFSET = timedelta(hours=9)
_KST = timezone(KST_OFFSET)

# 진료과 한글명 → 환경변수명 매핑 딕셔너리.
# 각 환경변수에는 Cal.com의 Event Type ID(정수 문자열)가 저장되어 있다.
# 예: CALCOM_ENT_ID=12345 → 이비인후과의 Cal.com 이벤트 타입 ID가 12345
# 호출 시점에 os.environ에서 lazy-read하므로, 런타임 중 환경변수 변경도 반영된다.
_DEPT_ENV_MAP: dict[str, str] = {
    "이비인후과": "CALCOM_ENT_ID",
    "내과": "CALCOM_INTERNAL_ID",
    "정형외과": "CALCOM_ORTHO_ID",
}


# ─────────────────────────────────────────────
# 내부 헬퍼
# ─────────────────────────────────────────────

def _api_key() -> str:
    """
    Cal.com 인증에 사용할 API 키를 환경변수에서 읽어 반환한다.

    환경변수 CALCOM_API_KEY가 설정되어 있지 않으면 빈 문자열("")을 반환한다.
    빈 문자열은 is_calcom_enabled() 등에서 falsy로 평가되어
    Graceful Degradation(연동 비활성) 분기를 타게 된다.

    Returns
    -------
    str
        CALCOM_API_KEY 환경변수 값. 미설정 시 빈 문자열.
    """
    return os.environ.get("CALCOM_API_KEY", "")


def _event_type_id(department: str) -> str:
    """
    진료과(department)에 대응하는 Cal.com Event Type ID를 환경변수에서 읽어 반환한다.

    _DEPT_ENV_MAP에서 진료과명으로 환경변수명을 조회한 뒤, 해당 환경변수의 값을
    반환한다. 진료과명이 매핑에 없거나, 환경변수가 설정되어 있지 않으면
    빈 문자열("")을 반환한다.

    Parameters
    ----------
    department : str
        진료과 한글명 (예: "이비인후과", "내과", "정형외과")

    Returns
    -------
    str
        Cal.com Event Type ID 문자열. 매핑 실패 시 빈 문자열.
    """
    env_name = _DEPT_ENV_MAP.get(department, "")
    if not env_name:
        return ""
    return os.environ.get(env_name, "")


def _make_dummy_email(patient_contact: str) -> str:
    """
    환자 연락처(전화번호)로부터 더미 이메일 주소를 생성한다.

    Cal.com API는 attendee(참석자) 생성 시 email 필드가 필수이다.
    그러나 실제 병원 예약에서는 이메일이 아닌 전화번호를 사용하므로,
    전화번호의 숫자만 추출하여 '@kobimedi.local' 도메인의 더미 이메일을 만든다.

    예시: "010-1234-5678" → "01012345678@kobimedi.local"
          None 또는 "" → "unknown@kobimedi.local"

    Parameters
    ----------
    patient_contact : str
        환자 연락처 문자열 (전화번호). None이어도 안전하게 처리된다.

    Returns
    -------
    str
        Cal.com attendee.email 필드에 사용할 더미 이메일 주소.
    """
    digits = "".join(c for c in (patient_contact or "") if c.isdigit())
    return f"{digits or 'unknown'}@kobimedi.local"


def _kst_to_utc_iso(date: str, time: str) -> str:
    """
    KST(한국 표준시) 날짜+시간 문자열을 UTC ISO 8601 형식으로 변환한다.

    Cal.com API의 booking 생성 시 start 필드는 UTC 시간을 요구한다.
    사용자가 입력한 KST 시간(예: 14:00)을 UTC(예: 05:00)로 변환하여
    Cal.com이 이해할 수 있는 형식으로 만든다.

    변환 과정:
      1. date("2024-01-15") + time("14:00") → "2024-01-15T14:00:00" (KST)
      2. KST 타임존 정보 부착 → UTC로 변환
      3. "2024-01-15T05:00:00.000Z" 형식의 문자열 반환

    Parameters
    ----------
    date : str
        날짜 문자열. "YYYY-MM-DD" 형식 (예: "2024-01-15")
    time : str
        시간 문자열. "HH:MM" 형식 (예: "14:00")

    Returns
    -------
    str
        UTC ISO 8601 형식의 시간 문자열 (예: "2024-01-15T05:00:00.000Z")

    Raises
    ------
    ValueError
        date 또는 time 형식이 올바르지 않을 때
    TypeError
        date 또는 time이 None일 때
    """
    dt_kst = datetime.fromisoformat(f"{date}T{time}:00").replace(tzinfo=_KST)
    dt_utc = dt_kst.astimezone(timezone.utc)
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _common_headers(api_version: str) -> dict[str, str]:
    """
    Cal.com API 요청에 공통으로 사용되는 HTTP 헤더 딕셔너리를 생성한다.

    모든 Cal.com v2 API 요청에는 다음 세 가지 헤더가 필요하다:
      - Authorization: Bearer 토큰 인증
      - cal-api-version: Cal.com API 버전 지정 (엔드포인트마다 다를 수 있음)
      - Content-Type: JSON 형식 명시

    Parameters
    ----------
    api_version : str
        Cal.com API 버전 문자열 (예: "2024-08-13", "2024-09-04").
        엔드포인트별로 지원 버전이 다르므로 호출부에서 지정한다.

    Returns
    -------
    dict[str, str]
        Authorization, cal-api-version, Content-Type 키를 포함하는 헤더 딕셔너리.
    """
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
    Cal.com 연동이 활성 상태인지 여부를 판단하여 반환한다.

    Graceful Degradation의 핵심 게이트 함수이다.
    다른 공개 함수들이 실제 API 호출 전에 이 함수를 먼저 호출하여,
    환경이 준비되지 않았으면 즉시 None을 반환하고 종료한다.

    판단 기준:
      1. CALCOM_API_KEY 환경변수가 비어 있으면 → False (Cal.com 미연동 환경)
      2. department가 지정된 경우, 해당 진료과의 Event Type ID 환경변수가
         비어 있으면 → False (해당 진료과 미설정)
      3. 위 조건을 모두 통과하면 → True

    Parameters
    ----------
    department : str | None, optional
        검사할 진료과명. None이면 API 키만 확인한다.

    Returns
    -------
    bool
        True이면 Cal.com API 호출 가능, False이면 불가능.
    """
    if not _api_key():
        return False
    if department is not None:
        if not _event_type_id(department):
            return False
    return True


def get_available_slots(department: str, target_date: str) -> Optional[list[str]]:
    """
    Cal.com GET /slots API를 호출해 특정 날짜의 가용 예약 시간 목록을 조회한다.

    지정된 진료과의 Event Type에 대해 target_date 하루 동안(00:00~23:59 KST)
    예약 가능한 시간 슬롯을 조회한다. Cal.com은 UTC 기반으로 슬롯을 반환하므로,
    각 슬롯의 시작 시간을 KST로 변환하여 "HH:MM" 형식의 문자열 리스트로 반환한다.

    Cal.com v2 응답 구조 예시:
      {"status": "success", "data": {"2024-01-15": [{"start": "2024-01-15T05:00:00Z"}, ...]}}

    Parameters
    ----------
    department : str
        진료과 한글명 (예: "이비인후과")
    target_date : str
        조회 대상 날짜. "YYYY-MM-DD" 형식 (예: "2024-01-15")

    Returns
    -------
    list[str]
        가용 슬롯의 KST 시간 목록 (예: ["09:00", "10:00", "14:00"]).
        빈 리스트([])는 해당 날짜에 예약 가능한 시간이 없음을 의미한다.
    None
        API 비활성(Graceful Degradation) 또는 네트워크/타임아웃/파싱 오류 시
        Hard Fail을 나타낸다. agent.py는 이를 받아 AGENT_HARD_FAIL을 반환한다.
    """
    if not is_calcom_enabled(department):
        return None

    etype_id = _event_type_id(department)
    if not etype_id:
        return None

    url = f"{CALCOM_BASE_URL}/slots"
    headers = _common_headers("2024-09-04")
    # KST 기준 하루 전체를 조회 범위로 설정한다.
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

        # 각 슬롯의 UTC 시작 시간을 KST "HH:MM" 형식으로 변환한다.
        result: list[str] = []
        for slot in raw_slots:
            raw_time = slot.get("start", "")
            if not raw_time:
                continue
            try:
                dt = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
                result.append(dt.astimezone(_KST).strftime("%H:%M"))
            except (ValueError, AttributeError):
                # 파싱 불가능한 슬롯은 건너뛴다 (부분 실패 허용).
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
    Cal.com POST /bookings API를 호출해 새로운 예약을 생성한다.

    지정된 진료과, 날짜, 시간에 환자 정보를 포함한 예약을 생성한다.
    시간은 KST → UTC 변환 후 Cal.com에 전달되며, attendee.email은
    전화번호 기반 더미 이메일로 채운다.

    Race Condition 처리:
      - Cal.com이 409 Conflict를 반환하면 다른 사용자가 해당 슬롯을 이미
        선점했음을 의미한다 → False 반환.
      - Cal.com v2는 슬롯 중복 시 409 대신 400 + "already has booking" 또는
        "not available" 메시지를 반환하기도 한다 → 동일하게 False 반환.

    Parameters
    ----------
    department : str
        진료과 한글명 (예: "이비인후과")
    date : str
        예약 날짜. "YYYY-MM-DD" 형식 (예: "2024-01-15")
    time : str
        예약 시간(KST). "HH:MM" 형식 (예: "14:00")
    patient_name : str
        환자 이름. 비어 있으면 "환자"로 대체된다.
    patient_contact : str
        환자 연락처(전화번호). 더미 이메일 생성 및 metadata에 사용된다.
    customer_type : str, optional
        고객 유형. "new"(신규) 또는 "existing"(기존). 기본값 "new".
        metadata에 저장되어 병원 측에서 참조할 수 있다.

    Returns
    -------
    dict
        성공 시 Cal.com 응답 데이터 (booking ID, UID 등 포함).
    False
        409/400 Conflict → 슬롯 선점(Race Condition) 발생.
        agent.py는 이를 받아 사용자에게 다른 시간을 안내한다.
    None
        API 비활성(Graceful Degradation) 또는 네트워크/타임아웃 오류 → Hard Fail.
        agent.py는 이를 받아 AGENT_HARD_FAIL을 반환한다.
    """
    if not is_calcom_enabled(department):
        return None

    etype_id = _event_type_id(department)
    if not etype_id:
        return None

    # KST → UTC 변환: Cal.com API는 UTC 시간을 요구한다.
    try:
        start_utc = _kst_to_utc_iso(date, time)
    except (ValueError, TypeError) as exc:
        logger.error("AGENT_HARD_FAIL: cal.com create_booking time conversion error: %s", exc)
        return None

    dummy_email = _make_dummy_email(patient_contact)
    url = f"{CALCOM_BASE_URL}/bookings"
    headers = _common_headers("2024-08-13")
    # Cal.com POST /bookings 요청 본문 구성
    payload = {
        "eventTypeId": int(etype_id),
        "start": start_utc,
        "attendee": {
            "name": patient_name or "환자",
            "email": dummy_email,
            "timeZone": "Asia/Seoul",
        },
        # metadata: Cal.com 예약에 추가 정보를 저장한다.
        # 병원 측 관리 화면에서 환자 연락처, 진료과, 고객 유형을 확인할 수 있다.
        "metadata": {
            "patient_contact": patient_contact,
            "department": department,
            "customer_type": customer_type,
        },
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)

        # 409 Conflict: 다른 사용자가 동일 슬롯을 먼저 예약한 경우 (Race Condition)
        if response.status_code == 409:
            logger.warning(
                "cal.com create_booking 409 Conflict (Race Condition): dept=%s date=%s time=%s",
                department, date, time,
            )
            return False  # 슬롯 선점 신호

        # Cal.com v2는 슬롯 중복 시 409 대신 400 + 특정 메시지를 반환하기도 한다.
        # "already has booking" 또는 "not available" 포함 시 동일하게 Race Condition으로 처리한다.
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
    Cal.com GET /bookings API를 호출해 예정된(upcoming) 전체 예약 목록을 반환한다.

    status=upcoming 파라미터를 사용하여 아직 진행되지 않은 예약만 조회한다.
    이 함수는 예약 조회(check) 기능에서 사용되며, 로컬 저장소와 Cal.com 간
    데이터 정합성 확인에도 활용될 수 있다.

    주의: 이 함수는 department별 필터링 없이 전체 예약을 가져온다.
    is_calcom_enabled(department) 대신 _api_key()만 확인한다.

    Returns
    -------
    list[dict]
        예약 목록. 각 항목에 id, uid, title, start, end, status 등이 포함된다.
        예약이 없으면 빈 리스트([])를 반환한다.
    None
        API 비활성(Graceful Degradation) 또는 네트워크/타임아웃 오류 → Hard Fail.
    """
    if not _api_key():
        return None

    url = f"{CALCOM_BASE_URL}/bookings"
    headers = _common_headers("2024-08-13")
    # status=upcoming: 아직 진행되지 않은 예약만 조회한다.
    params = {"status": "upcoming"}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        bookings = data.get("data", data)
        # 응답이 리스트가 아닌 경우(예상 외 구조) 빈 리스트로 안전하게 처리한다.
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
    Cal.com POST /bookings/{uid}/cancel API를 호출해 원격 예약을 취소한다.

    booking_uid는 Cal.com이 예약 생성 시 발급하는 고유 식별자(UUID 형식)이다.
    로컬 저장소에 저장된 calcom_uid 필드의 값을 이 함수에 전달한다.

    멱등성(Idempotency) 보장:
      - 이미 취소된 예약에 대해 다시 취소 요청을 보내도 404가 반환되며,
        이 경우에도 True를 반환하여 "취소 완료" 상태로 처리한다.
      - 200, 204, 404 모두 True로 처리하므로, 중복 취소 요청에도 안전하다.

    Parameters
    ----------
    booking_uid : str
        Cal.com 예약 고유 식별자 (UUID 형식).
        빈 문자열이나 None이면 즉시 None을 반환한다.

    Returns
    -------
    True
        취소 성공 또는 이미 취소된 상태 (멱등성 보장).
    None
        API 비활성(Graceful Degradation) 또는 네트워크/타임아웃 오류 → Hard Fail.
        agent.py는 이를 받아 거짓 성공 없이 AGENT_HARD_FAIL을 반환한다.
    """
    if not _api_key():
        return None
    if not booking_uid:
        return None

    url = f"{CALCOM_BASE_URL}/bookings/{booking_uid}/cancel"
    headers = _common_headers("2024-08-13")

    try:
        # Cal.com의 취소 API는 DELETE가 아닌 POST 메서드를 사용한다.
        response = requests.post(
            url, headers=headers,
            json={"cancellationReason": "cleanup"},
            timeout=10,
        )

        # 200/204: 정상 취소 완료
        if response.status_code in (200, 204):
            return True

        # 404: 해당 예약이 이미 삭제되었거나 존재하지 않음 → 취소 완료로 간주
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
