"""
로컬 JSON 저장소 모듈 (bookings.json)

이 모듈은 예약 챗봇 시스템에서 모든 예약 데이터의 **단일 진실 원천(Single Source of Truth)**
역할을 하는 로컬 JSON 파일(bookings.json)을 관리한다.

주요 설계 원칙:
- create_booking은 **원자적 쓰기(atomic write)**를 사용한다.
  임시 파일에 먼저 기록한 뒤 rename으로 교체하여, 쓰기 도중 프로세스가 중단되어도
  기존 데이터가 손상되지 않도록 보장한다.
- cancel_booking은 예약을 삭제하지 않고 status를 "cancelled"로 변경한다.
  이력 추적과 초진/재진 판별을 위해 취소된 예약도 보존해야 하기 때문이다.
- find_bookings는 이름, 연락처, 진료과, 날짜, 시간, 생년월일 등 다양한 필터로
  예약을 검색할 수 있다.
- resolve_customer_type_from_history는 과거 예약 이력을 기반으로
  환자가 초진(첫 방문)인지 재진(재방문)인지를 결정론적으로 판별한다.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


# 기본 예약 저장 경로: 프로젝트 루트의 data/bookings.json
DEFAULT_BOOKINGS_PATH = Path(__file__).resolve().parent.parent / "data" / "bookings.json"

# 예약 레코드에 반드시 존재해야 하는 필수 필드 목록.
# _prepare_booking_record에서 이 목록을 기준으로 유효성 검증을 수행한다.
REQUIRED_BOOKING_FIELDS = (
    "id",
    "patient_name",
    "patient_contact",
    "is_proxy_booking",
    "booking_time",
    "department",
    "customer_type",
    "status",
)


class StorageError(Exception):
    """Base exception for persistent booking storage failures."""


class StorageReadError(StorageError):
    """Raised when bookings.json cannot be read safely."""


class StorageDecodeError(StorageReadError):
    """Raised when bookings.json is corrupted or not valid JSON."""


class StorageWriteError(StorageError):
    """Raised when bookings.json cannot be written atomically."""


class StorageConflictError(StorageError):
    """Raised when a fresh storage recheck blocks persistence."""


class StorageValidationError(StorageError):
    """Raised when a new booking record is missing required fields."""


def _resolve_path(path: str | Path | None = None) -> Path:
    """
    저장소 파일 경로를 결정한다.

    인자로 경로가 주어지면 해당 경로를 Path 객체로 변환하여 반환하고,
    None이면 기본 경로(DEFAULT_BOOKINGS_PATH)를 반환한다.

    시스템 내 역할:
        모든 저장소 함수(load_bookings, save_bookings 등)가 호출 시 경로를
        일관되게 해석하도록 하는 중앙 경로 해석 헬퍼이다.
        테스트 시 임시 경로를 주입할 수 있게 해주는 역할도 한다.
    """
    if path is None:
        return DEFAULT_BOOKINGS_PATH
    return Path(path)


def normalize_patient_contact(value: Any) -> str | None:
    """
    환자 연락처(전화번호)를 정규화한다.

    동작 흐름:
        1. None이나 빈 문자열이면 None을 반환한다.
        2. 입력값에서 숫자만 추출한다 (하이픈, 공백 등 제거).
        3. 숫자가 10자리 또는 11자리인 경우에만 유효한 한국 전화번호로 간주하여
           숫자만으로 이루어진 문자열을 반환한다.
        4. 그 외의 경우 None을 반환한다.

    시스템 내 역할:
        사용자가 "010-1234-5678", "01012345678", "010 1234 5678" 등 다양한
        형식으로 전화번호를 입력할 수 있으므로, 예약 검색·중복 확인·초재진 판별 시
        일관된 비교가 가능하도록 순수 숫자 문자열로 통일한다.
    """
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    digits_only = "".join(ch for ch in text if ch.isdigit())
    if len(digits_only) not in {10, 11}:
        return None
    return digits_only


def _safe_remove(path: Path | None) -> None:
    """
    파일을 안전하게 삭제한다. 삭제 실패 시에도 예외를 발생시키지 않는다.

    동작 흐름:
        1. path가 None이면 아무것도 하지 않는다.
        2. 파일이 존재하면 삭제를 시도한다.
        3. OSError가 발생하더라도 조용히 무시한다.

    시스템 내 역할:
        save_bookings에서 원자적 쓰기 도중 오류가 발생했을 때, 남아 있는
        임시 파일을 정리하는 데 사용된다. 임시 파일 정리 실패가 본래 에러를
        가리지 않도록 예외를 억제한다.
    """
    if path is None:
        return
    try:
        if path.exists():
            path.unlink()
    except OSError:
        return


def _get_booking_patient_name(booking: dict[str, Any]) -> str | None:
    """
    예약 레코드에서 환자 이름을 추출한다.

    동작 흐름:
        1. 먼저 "patient_name" 필드를 확인하고, 유효한 문자열이면 반환한다.
        2. "patient_name"이 없으면 "customer_name" 필드를 대안으로 확인한다.
        3. 둘 다 없거나 빈 문자열이면 None을 반환한다.

    시스템 내 역할:
        초기 구현에서 "customer_name"을 사용했다가 "patient_name"으로 변경된
        이력이 있어, 하위 호환성을 유지하기 위한 폴백(fallback) 로직이다.
        find_bookings, _prepare_booking_record 등 여러 함수에서 환자 이름을
        안전하게 꺼내기 위해 사용된다.
    """
    patient_name = booking.get("patient_name")
    if patient_name is not None and str(patient_name).strip():
        return str(patient_name).strip()

    customer_name = booking.get("customer_name")
    if customer_name is not None and str(customer_name).strip():
        return str(customer_name).strip()
    return None


def load_bookings(path: str | Path | None = None) -> list[dict]:
    """
    bookings.json 파일에서 전체 예약 목록을 읽어온다.

    동작 흐름:
        1. 파일이 존재하지 않으면 빈 리스트를 반환한다 (최초 실행 시).
        2. 파일 읽기에 실패하면 StorageReadError를 발생시킨다.
        3. 파일 내용이 비어 있으면 빈 리스트를 반환한다.
        4. JSON 파싱에 실패하면 빈 리스트를 반환한다 (손상된 파일 허용).
        5. 최상위가 리스트가 아니거나, 리스트 내에 dict가 아닌 항목이 있으면
           빈 리스트를 반환한다 (예상 밖의 형식 방어).
        6. 정상적인 경우 각 dict를 복사하여 반환한다 (원본 변경 방지).

    시스템 내 역할:
        모든 예약 조회·생성·취소 작업의 출발점이다.
        저장소가 "진실 원천"이므로 이 함수를 통해 항상 최신 상태를 읽어야 한다.
    """
    bookings_path = _resolve_path(path)
    try:
        raw_text = bookings_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise StorageReadError(f"Failed to read bookings from {bookings_path}") from exc

    if not raw_text.strip():
        return []

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return []

    if not isinstance(data, list):
        return []

    invalid_items = [item for item in data if not isinstance(item, dict)]
    if invalid_items:
        return []

    return [dict(item) for item in data]


def save_bookings(bookings: list[dict], path: str | Path | None = None) -> bool:
    """
    예약 목록을 bookings.json 파일에 원자적으로 저장한다.

    동작 흐름:
        1. 상위 디렉터리가 없으면 생성한다.
        2. 같은 디렉터리에 임시 파일을 만들고 JSON 데이터를 기록한다.
        3. fsync로 디스크에 확실히 기록한 뒤, rename(replace)으로
           기존 파일을 교체한다.
        4. 실패 시 임시 파일을 정리하고 False를 반환한다.

    시스템 내 역할:
        **원자적 쓰기(atomic write)** 패턴을 구현하는 핵심 함수이다.
        임시 파일에 먼저 쓴 뒤 rename하므로, 쓰기 도중 프로세스가 중단되어도
        bookings.json이 절반만 쓰인 상태(corruption)가 되지 않는다.
        create_booking과 cancel_booking이 이 함수를 통해 변경사항을 영속화한다.
    """
    bookings_path = _resolve_path(path)
    bookings_path.parent.mkdir(parents=True, exist_ok=True)

    temp_path: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=str(bookings_path.parent),
        ) as temp_file:
            json.dump(bookings, temp_file, ensure_ascii=False, indent=2)
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_path = Path(temp_file.name)

        temp_path.replace(bookings_path)
        return True
    except OSError:
        _safe_remove(temp_path)
        return False
    except (TypeError, ValueError):
        _safe_remove(temp_path)
        return False


def _next_booking_id(bookings: list[dict]) -> str:
    """
    다음 예약 ID를 생성한다.

    동작 흐름:
        1. 기존 예약 목록에서 "booking-NNN" 형식의 ID를 모두 찾는다.
        2. 가장 큰 번호(NNN)를 찾아 1을 더한다.
        3. 3자리 제로패딩 형식으로 반환한다 (예: "booking-001", "booking-012").

    시스템 내 역할:
        예약 ID의 자동 생성을 담당한다. 기존 예약과 겹치지 않는 순차적
        ID를 보장하며, 사용자가 예약 ID를 직접 지정하지 않을 때 호출된다.
    """
    max_index = 0
    for booking in bookings:
        booking_id = str(booking.get("id") or "")
        if "-" not in booking_id:
            continue
        suffix = booking_id.rsplit("-", 1)[-1]
        if suffix.isdigit():
            max_index = max(max_index, int(suffix))
    return f"booking-{max_index + 1:03d}"


def normalize_birth_date(value: Any) -> str | None:
    """
    다양한 형식의 생년월일 입력을 ISO 형식(YYYY-MM-DD)으로 정규화한다.

    동작 흐름:
        1. None이나 빈 문자열이면 None을 반환한다.
        2. 순수 숫자 8자리(예: "19900315")이면 "YYYY-MM-DD" 형식으로 변환을
           시도한다.
        3. "년/월/일", ".", "/" 등의 구분자를 "-"로 통일하여 파싱을 시도한다.
        4. 어떤 형식으로도 파싱에 실패하면 None을 반환한다.

    시스템 내 역할:
        사용자가 생년월일을 "1990.03.15", "1990년 3월 15일", "19900315" 등
        다양하게 입력할 수 있으므로, 예약 검색·동명이인 구별·초재진 판별 시
        일관된 비교가 가능하도록 표준 형식으로 통일한다.
    """
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    digits_only = "".join(ch for ch in text if ch.isdigit())
    candidate_formats: list[str] = []

    if len(digits_only) == 8:
        candidate_formats.append(f"{digits_only[:4]}-{digits_only[4:6]}-{digits_only[6:8]}")

    normalized_text = (
        text.replace("년", "-")
        .replace("월", "-")
        .replace("일", "")
        .replace(".", "-")
        .replace("/", "-")
        .strip()
    )
    candidate_formats.append(normalized_text)

    for candidate in candidate_formats:
        try:
            return datetime.strptime(candidate, "%Y-%m-%d").date().isoformat()
        except ValueError:
            continue
    return None


def _extract_booking_date_time(booking: dict) -> tuple[str | None, str | None]:
    """
    예약 레코드에서 날짜와 시간을 분리하여 추출한다.

    동작 흐름:
        1. "booking_time" 필드(ISO 형식 datetime)가 있으면 파싱하여
           날짜(YYYY-MM-DD)와 시간(HH:MM)을 분리 반환한다.
        2. UTC "Z" 접미사를 "+00:00"으로 변환하여 fromisoformat 호환성을 확보한다.
        3. "booking_time"이 없거나 파싱에 실패하면 "date"와 "time" 필드를
           직접 반환한다 (레거시 형식 대응).

    시스템 내 역할:
        find_bookings에서 날짜/시간 필터링 시 사용된다.
        예약 데이터가 "booking_time"(ISO datetime) 또는 별도의 "date"/"time"
        필드 중 어느 형식이든 통일된 방식으로 비교할 수 있게 해준다.
    """
    booking_time = booking.get("booking_time")
    if booking_time:
        raw_value = str(booking_time)
        if raw_value.endswith("Z"):
            raw_value = f"{raw_value[:-1]}+00:00"
        try:
            dt = datetime.fromisoformat(raw_value)
        except ValueError:
            dt = None
        if dt is not None:
            return dt.date().isoformat(), dt.strftime("%H:%M")

    return booking.get("date"), booking.get("time")


def _validate_required_field(booking: dict[str, Any], field_name: str) -> None:
    """
    예약 레코드의 특정 필드가 필수값을 만족하는지 검증한다.

    동작 흐름:
        1. "is_proxy_booking" 필드는 bool이므로, 키 존재 여부만 확인한다.
        2. 그 외 필드는 값이 None이거나 빈 문자열이면 StorageValidationError를
           발생시킨다.

    시스템 내 역할:
        _prepare_booking_record에서 REQUIRED_BOOKING_FIELDS의 각 필드에 대해
        호출되며, 불완전한 예약이 저장소에 기록되는 것을 방지하는 게이트 역할을 한다.
    """
    if field_name == "is_proxy_booking":
        if field_name not in booking:
            raise StorageValidationError(f"Missing required booking field: {field_name}")
        return

    value = booking.get(field_name)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise StorageValidationError(f"Missing required booking field: {field_name}")


def _prepare_booking_record(record: dict[str, Any], bookings: list[dict]) -> dict[str, Any]:
    """
    사용자 입력 레코드를 저장 가능한 완전한 예약 레코드로 가공한다.

    동작 흐름:
        1. 입력이 dict인지 확인한다.
        2. 환자 이름을 추출하고, 없으면 검증 에러를 발생시킨다.
        3. 연락처를 정규화하고, 유효하지 않으면 검증 에러를 발생시킨다.
        4. ID가 없으면 _next_booking_id로 자동 생성한다.
        5. patient_name, customer_name, patient_contact, is_proxy_booking,
           status, created_at, updated_at 등 기본값을 설정한다.
        6. 생년월일이 있으면 정규화하고, 빈 값이면 필드를 제거한다.
        7. REQUIRED_BOOKING_FIELDS의 모든 필드에 대해 유효성을 검증한다.

    시스템 내 역할:
        create_booking의 핵심 전처리 단계로, 다양한 형식의 사용자 입력을
        일관된 스키마의 예약 레코드로 변환한다. 필수 필드 누락 시
        StorageValidationError를 발생시켜 불완전한 데이터가 저장되는 것을 방지한다.
    """
    if not isinstance(record, dict):
        raise StorageValidationError("Booking record must be a dictionary")

    now = datetime.now(timezone.utc).isoformat()
    booking = dict(record)

    patient_name = _get_booking_patient_name(booking)
    if not patient_name:
        raise StorageValidationError("Missing required booking field: patient_name")

    normalized_contact = normalize_patient_contact(booking.get("patient_contact"))
    if normalized_contact is None:
        raise StorageValidationError("Missing required booking field: patient_contact")

    booking["id"] = str(booking.get("id") or _next_booking_id(bookings))
    booking["patient_name"] = patient_name
    booking.setdefault("customer_name", patient_name)
    booking["patient_contact"] = str(booking.get("patient_contact")).strip()
    booking["is_proxy_booking"] = bool(booking.get("is_proxy_booking", False))
    booking["status"] = str(booking.get("status") or "active")
    booking["created_at"] = str(booking.get("created_at") or now)
    booking["updated_at"] = now

    normalized_birth_date = normalize_birth_date(booking.get("birth_date"))
    if normalized_birth_date:
        booking["birth_date"] = normalized_birth_date
    elif "birth_date" in booking and booking.get("birth_date") in {"", None}:
        booking.pop("birth_date", None)

    for field_name in REQUIRED_BOOKING_FIELDS:
        _validate_required_field(booking, field_name)

    return booking


def _has_active_duplicate_booking(booking: dict[str, Any], bookings: list[dict]) -> bool:
    """
    동일한 환자가 같은 시간·진료과에 이미 활성 예약을 가지고 있는지 확인한다.

    동작 흐름:
        1. 새 예약의 연락처, 시간, 진료과를 추출한다.
        2. 셋 중 하나라도 없으면 중복 판단 불가로 False를 반환한다.
        3. 기존 예약 목록을 순회하면서 status가 "active"이고
           연락처·시간·진료과가 모두 일치하는 예약이 있으면 True를 반환한다.

    시스템 내 역할:
        같은 환자가 동일 시간에 동일 진료과에 이중 예약하는 것을 방지하는
        중복 방어 로직이다. _recheck_before_persist에서 저장 직전에 호출된다.
    """
    booking_contact = normalize_patient_contact(booking.get("patient_contact"))
    booking_time = booking.get("booking_time")
    booking_department = booking.get("department")

    if not booking_contact or not booking_time or not booking_department:
        return False

    for existing_booking in bookings:
        if existing_booking.get("status", "active") != "active":
            continue
        if normalize_patient_contact(existing_booking.get("patient_contact")) != booking_contact:
            continue
        if existing_booking.get("booking_time") != booking_time:
            continue
        if existing_booking.get("department") != booking_department:
            continue
        return True

    return False


def _interpret_recheck_result(result: Any) -> tuple[bool, str | None]:
    """
    외부 가용성 재확인 콜백의 반환값을 (허용여부, 사유) 튜플로 해석한다.

    동작 흐름:
        1. None이면 허용(True, None)으로 해석한다.
        2. dict이면 "allowed" 키로 허용 여부, "reason"/"message"로 사유를 추출한다.
        3. tuple이면 첫 번째 원소를 허용 여부, 두 번째를 사유로 해석한다.
        4. bool이면 그대로 허용 여부로 사용한다.
        5. 그 외 타입이면 truthy 여부로 판단한다.

    시스템 내 역할:
        create_booking에 주입되는 availability_rechecker 콜백의 반환 형식이
        다양할 수 있으므로, 어떤 형식이든 일관된 (bool, str|None) 결과로
        변환하는 어댑터 역할을 한다.
    """
    if result is None:
        return True, None

    if isinstance(result, dict):
        return bool(result.get("allowed", True)), result.get("reason") or result.get("message")

    if isinstance(result, tuple) and result:
        allowed = bool(result[0])
        reason = str(result[1]) if len(result) > 1 and result[1] is not None else None
        return allowed, reason

    if isinstance(result, bool):
        return result, None

    return bool(result), None


def _recheck_before_persist(
    booking: dict[str, Any],
    bookings: list[dict],
    availability_rechecker: Callable[[dict[str, Any], list[dict]], Any] | None = None,
) -> None:
    """
    예약을 저장소에 기록하기 직전에 최종 검증을 수행한다.

    동작 흐름:
        1. 먼저 _has_active_duplicate_booking으로 중복 예약을 검사한다.
           중복이 발견되면 StorageConflictError를 발생시킨다.
        2. availability_rechecker 콜백이 제공된 경우 이를 호출하여
           외부 가용성(예: Cal.com 슬롯, 3명 제한 등)을 다시 확인한다.
        3. 콜백이 거부하면 StorageConflictError를 발생시킨다.

    시스템 내 역할:
        create_booking에서 **저장 직전에** 호출되는 최종 안전 게이트이다.
        예약 준비(prepare)와 실제 저장(persist) 사이의 시간차에서 발생할 수 있는
        경쟁 조건(race condition)을 최소화하기 위해, 최신 데이터를 기반으로
        한 번 더 검증한다.
    """
    if _has_active_duplicate_booking(booking, bookings):
        raise StorageConflictError("이미 동일 환자에 대한 활성 예약이 같은 시간에 존재합니다.")

    if availability_rechecker is None:
        return

    allowed, reason = _interpret_recheck_result(
        availability_rechecker(dict(booking), [dict(item) for item in bookings])
    )
    if not allowed:
        raise StorageConflictError(reason or "최종 저장 직전 저장소 재검증에 실패했습니다.")


def create_booking(
    record: dict[str, Any],
    path: str | Path | None = None,
    *,
    availability_rechecker: Callable[[dict[str, Any], list[dict]], Any] | None = None,
) -> dict[str, Any]:
    """
    새 예약을 생성하여 저장소에 원자적으로 기록한다.

    동작 흐름:
        1. 현재 저장소를 읽어 예약 레코드를 준비한다 (ID 생성, 필드 정규화).
        2. 저장소를 다시 한 번 읽어 최신 상태를 확보한다 (경쟁 조건 최소화).
        3. 원래 레코드에 ID가 없었으면, 최신 데이터 기준으로 ID를 재생성한다.
        4. _recheck_before_persist로 중복·가용성을 최종 검증한다.
        5. 검증을 통과하면 예약을 추가하고 save_bookings로 원자적 저장한다.
        6. 저장에 실패하면 StorageWriteError를 발생시킨다.

    시스템 내 역할:
        예약 생성의 전체 파이프라인을 조율하는 공개 함수이다.
        "두 번 읽기" 전략으로 경쟁 조건을 줄이고, 원자적 쓰기로 데이터 무결성을
        보장하며, 저장 실패 시 명확한 에러를 발생시켜 "거짓 성공"을 방지한다.
    """
    # NOTE: This initial load is for optimistic ID generation and preparation.
    # The critical re-check happens right before persistence.
    initial_bookings = load_bookings(path)
    booking = _prepare_booking_record(record, initial_bookings)

    # Freshly load the bookings again to minimize race conditions.
    rechecked_bookings = load_bookings(path)

    # If the original record didn't have an ID, generate a new one based on the fresh data.
    if "id" not in record or not record.get("id"):
        booking["id"] = _next_booking_id(rechecked_bookings)

    _recheck_before_persist(booking, rechecked_bookings, availability_rechecker)

    rechecked_bookings.append(booking)
    if not save_bookings(rechecked_bookings, path):
        raise StorageWriteError(f"Failed to write bookings to {_resolve_path(path)}")

    return booking


def find_bookings(
    customer_name: str | None = None,
    filters: dict[str, Any] | None = None,
    path: str | Path | None = None,
    include_cancelled: bool = False,
    patient_contact: str | None = None,
) -> list[dict]:
    """
    다양한 조건으로 예약을 검색한다.

    동작 흐름:
        1. 저장소에서 전체 예약 목록을 로드한다.
        2. 각 예약에 대해 다음 필터를 순서대로 적용한다:
           - status: 요청된 상태와 일치하는지, 또는 include_cancelled가 아니면
             "active"인지 확인한다.
           - 이름: customer_name이 주어지고 연락처 필터가 없으면 이름으로 필터링한다.
           - 연락처: patient_contact가 주어지면 정규화된 전화번호로 비교한다.
           - id, department, booking_time: filters dict의 해당 키로 정확 매칭한다.
           - birth_date: 정규화된 생년월일로 비교한다.
           - date, time: booking_time에서 추출한 날짜/시간 또는 별도 필드로 비교한다.
        3. 모든 필터를 통과한 예약만 결과 리스트에 포함한다.

    시스템 내 역할:
        예약 조회, 변경, 취소, 초재진 판별 등 거의 모든 비즈니스 로직에서
        호출되는 범용 검색 함수이다. 저장소를 "진실 원천"으로 사용하므로,
        LLM이 아닌 이 함수의 결과를 기반으로 의사결정이 이루어진다.
    """
    filters = filters or {}
    results: list[dict] = []
    normalized_birth_date = normalize_birth_date(filters.get("birth_date"))
    requested_status = filters.get("status")
    requested_contact = normalize_patient_contact(patient_contact or filters.get("patient_contact"))

    for booking in load_bookings(path):
        booking_status = booking.get("status", "active")
        if requested_status:
            if booking_status != requested_status:
                continue
        elif not include_cancelled and booking_status != "active":
            continue

        booking_name = _get_booking_patient_name(booking)
        if (
            not requested_contact
            and customer_name
            and booking_name != customer_name
            and booking.get("customer_name") != customer_name
        ):
            continue
        if requested_contact and normalize_patient_contact(booking.get("patient_contact")) != requested_contact:
            continue
        if filters.get("id") and booking.get("id") != filters["id"]:
            continue
        if filters.get("department") and booking.get("department") != filters["department"]:
            continue
        if filters.get("booking_time") and booking.get("booking_time") != filters["booking_time"]:
            continue

        booking_birth_date = normalize_birth_date(booking.get("birth_date"))
        if normalized_birth_date and booking_birth_date != normalized_birth_date:
            continue

        booking_date, booking_time = _extract_booking_date_time(booking)
        if filters.get("date") and booking_date != filters["date"]:
            continue
        if filters.get("time") and booking_time != filters["time"]:
            continue

        results.append(dict(booking))

    return results


def list_patient_birth_dates(
    customer_name: str,
    path: str | Path | None = None,
) -> list[str]:
    """
    특정 이름의 환자가 과거에 사용한 모든 생년월일을 조회한다.

    동작 흐름:
        1. 해당 이름으로 취소 포함 전체 예약을 검색한다.
        2. 각 예약에서 생년월일을 정규화하여 중복 제거(set)한다.
        3. 정렬하여 반환한다.

    시스템 내 역할:
        동명이인 판별에 사용된다. 같은 이름으로 예약된 기록에 서로 다른
        생년월일이 있으면 동명이인일 가능성이 있으므로, 사용자에게
        생년월일 확인을 요청하는 근거 데이터를 제공한다.
    """
    birth_dates = {
        normalized_birth_date
        for booking in find_bookings(
            customer_name=customer_name,
            path=path,
            include_cancelled=True,
        )
        if (normalized_birth_date := normalize_birth_date(booking.get("birth_date")))
    }
    return sorted(birth_dates)


def _history_identity_keys(bookings: list[dict[str, Any]]) -> set[tuple[str, str]]:
    """
    예약 목록에서 고유 환자 식별 키 집합을 추출한다.

    동작 흐름:
        1. 각 예약에서 연락처가 있으면 ("patient_contact", 연락처)를 키로 사용한다.
        2. 연락처가 없고 생년월일이 있으면 ("birth_date", 생년월일)을 키로 사용한다.
        3. 둘 다 없으면 ("booking_id", 예약ID)를 키로 사용한다.
        4. 우선순위: 연락처 > 생년월일 > 예약ID

    시스템 내 역할:
        같은 이름으로 검색된 예약들이 실제로 같은 사람인지 판별하는 데 사용된다.
        키 집합의 크기가 2 이상이면 동명이인(ambiguous)으로 간주하여,
        resolve_customer_type_from_history에서 추가 정보 요청을 유도한다.
    """
    identity_keys: set[tuple[str, str]] = set()
    for booking in bookings:
        normalized_contact = normalize_patient_contact(booking.get("patient_contact"))
        normalized_birth_date = normalize_birth_date(booking.get("birth_date"))
        if normalized_contact:
            identity_keys.add(("patient_contact", normalized_contact))
            continue
        if normalized_birth_date:
            identity_keys.add(("birth_date", normalized_birth_date))
            continue
        identity_keys.add(("booking_id", str(booking.get("id") or "unknown")))
    return identity_keys


def _build_customer_type_result(
    matched_bookings: list[dict[str, Any]],
    *,
    ambiguous: bool = False,
    birth_date_candidates: list[str] | None = None,
) -> dict[str, Any]:
    """
    초진/재진 판별 결과를 표준 딕셔너리 형태로 구성한다.

    동작 흐름:
        1. 매칭된 예약 중 취소되지 않은 것이 있으면 has_non_cancelled_history = True
        2. 취소된 것이 있으면 has_cancelled_history = True
        3. ambiguous가 True이면 customer_type을 None으로 설정한다 (판단 불가).
        4. 그렇지 않으면 비취소 이력이 있으면 "재진", 없으면 "초진"으로 설정한다.

    시스템 내 역할:
        resolve_customer_type_from_history의 결과 생성을 담당하는 헬퍼이다.
        일관된 응답 구조를 보장하여, 호출자가 customer_type, ambiguous,
        birth_date_candidates 등을 예측 가능한 형식으로 받을 수 있게 한다.
    """
    has_non_cancelled_history = any(
        booking.get("status", "active") != "cancelled"
        for booking in matched_bookings
    )
    has_cancelled_history = any(
        booking.get("status", "active") == "cancelled"
        for booking in matched_bookings
    )

    return {
        "customer_type": None if ambiguous else ("재진" if has_non_cancelled_history else "초진"),
        "ambiguous": ambiguous,
        "birth_date_candidates": birth_date_candidates or [],
        "matched_bookings": matched_bookings if not ambiguous else [],
        "has_non_cancelled_history": has_non_cancelled_history if not ambiguous else False,
        "has_cancelled_history": has_cancelled_history if not ambiguous else False,
    }


def resolve_customer_type_from_history(
    customer_name: str | None = None,
    birth_date: str | None = None,
    path: str | Path | None = None,
    patient_contact: str | None = None,
) -> dict[str, Any]:
    """
    환자의 과거 예약 이력을 기반으로 초진(첫 방문) 또는 재진(재방문)을 판별한다.

    동작 흐름:
        1. 연락처가 주어지면 연락처로 검색하여 즉시 판별한다 (가장 신뢰도 높음).
        2. 이름이 없으면 판별 불가로 빈 결과를 반환한다.
        3. 이름으로 예약을 검색하고, 과거에 사용된 생년월일 목록을 수집한다.
        4. 생년월일이 주어지면 이름+생년월일로 정확 매칭하여 판별한다.
        5. 생년월일이 없으면 _history_identity_keys로 동명이인 여부를 확인한다.
           - 동명이인이면 ambiguous=True로 반환하여 추가 정보 요청을 유도한다.
           - 동명이인이 아니면 이름 검색 결과로 판별한다.

    시스템 내 역할:
        병원 예약 시 초진/재진 구분은 접수 절차와 비용에 영향을 미치므로
        정확한 판별이 필요하다. 이 함수는 LLM에 위임하지 않고 저장소 이력만으로
        결정론적으로 판별하며, 동명이인 등 모호한 경우에는 판단을 보류하고
        추가 정보를 요청하도록 설계되어 있다.
    """
    normalized_contact = normalize_patient_contact(patient_contact)
    if normalized_contact:
        matched_bookings = find_bookings(
            path=path,
            include_cancelled=True,
            patient_contact=patient_contact,
        )
        return _build_customer_type_result(matched_bookings)

    if not customer_name:
        return {
            "customer_type": None,
            "ambiguous": False,
            "birth_date_candidates": [],
            "matched_bookings": [],
            "has_non_cancelled_history": False,
            "has_cancelled_history": False,
        }

    matched_by_name = find_bookings(
        customer_name=customer_name,
        path=path,
        include_cancelled=True,
    )
    birth_date_candidates = list_patient_birth_dates(customer_name, path=path)
    normalized_birth_date = normalize_birth_date(birth_date)

    if normalized_birth_date:
        matched_bookings = [
            booking
            for booking in matched_by_name
            if normalize_birth_date(booking.get("birth_date")) == normalized_birth_date
        ]
        return _build_customer_type_result(
            matched_bookings,
            birth_date_candidates=birth_date_candidates,
        )

    ambiguous = len(_history_identity_keys(matched_by_name)) > 1
    if ambiguous:
        return _build_customer_type_result(
            [],
            ambiguous=True,
            birth_date_candidates=birth_date_candidates,
        )

    return _build_customer_type_result(
        matched_by_name,
        birth_date_candidates=birth_date_candidates,
    )


def cancel_booking(booking_id: str, path: str | Path | None = None) -> bool:
    """
    예약을 취소한다 (삭제가 아닌 상태 변경).

    동작 흐름:
        1. booking_id가 비어 있으면 False를 반환한다.
        2. 저장소에서 전체 예약을 로드한다.
        3. 해당 ID의 예약을 찾는다.
        4. 이미 "cancelled" 상태이면 True를 반환한다 (멱등성 보장).
        5. status를 "cancelled"로 변경하고 저장소에 기록한다.
        6. 해당 ID가 존재하지 않으면 False를 반환한다.

    시스템 내 역할:
        예약 취소 시 레코드를 삭제하지 않고 status만 변경하는 "소프트 삭제"
        방식을 사용한다. 이는 취소된 예약도 이력에 남겨두어
        resolve_customer_type_from_history에서 초진/재진 판별 시 참고할 수
        있도록 하기 위함이다. save_bookings를 통해 원자적으로 저장하므로
        저장 실패 시 False를 반환하여 "거짓 성공"을 방지한다.
    """
    if not booking_id:
        return False

    bookings = load_bookings(path)
    now = datetime.now(timezone.utc).isoformat()

    for booking in bookings:
        if booking.get("id") != booking_id:
            continue

        if booking.get("status") == "cancelled":
            return True

        booking["status"] = "cancelled"
        return save_bookings(bookings, path)

    return False
