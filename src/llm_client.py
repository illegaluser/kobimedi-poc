"""
Ollama LLM API 호출을 래핑하는 모듈.

이 모듈은 예약 챗봇 시스템에서 LLM(대규모 언어 모델)과의 통신을 전담한다.

핵심 구조:
- chat_json(): Ollama에 format='json' 옵션으로 요청을 보내고, 파싱된 dict를 반환하는 핵심 함수.
  JSON 파싱 실패 시 max_parse_retries만큼 재시도한다.
- chat_text(): JSON이 아닌 자연어 텍스트 응답이 필요할 때 사용하는 함수.
- build_classification_fallback() / build_safety_fallback(): LLM 호출 실패 시
  시스템이 크래시하지 않도록 안전한 기본값(fallback)을 제공하는 함수.

장애 처리 원칙:
- Ollama 연결 거부(connection refused), 타임아웃(timeout), 잘못된 JSON 등
  어떤 오류가 발생하더라도 예외를 상위로 전파하지 않고, 안전한 fallback 페이로드를 반환한다.
- 이를 통해 LLM 장애가 챗봇 전체 서비스 중단으로 이어지지 않도록 보장한다.
"""

import json
import socket

import ollama


MODEL_NAME = "qwen3-coder:30b"
TEMPORARY_ERROR_MESSAGE = "일시적 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."


def build_classification_fallback(action: str = "clarify") -> dict:
    """
    분류(classification) LLM 호출이 실패했을 때 사용할 안전한 기본 페이로드를 생성한다.

    목적:
        LLM이 사용자의 발화를 분류하지 못했을 때(연결 오류, 타임아웃 등),
        시스템이 크래시하지 않고 사용자에게 추가 정보를 요청(clarify)할 수 있도록
        모든 슬롯이 비어 있는 안전한 기본 딕셔너리를 반환한다.

    동작 흐름:
        1. action 파라미터를 기본값 "clarify"로 설정 (사용자에게 재질문).
        2. 예약에 필요한 모든 슬롯(department, doctor_name, date, time 등)을
           None 또는 빈 리스트로 초기화한 딕셔너리를 반환한다.
        3. is_emergency와 is_proxy_booking은 False로 설정하여
           긴급/대리 예약이 아닌 것으로 안전하게 간주한다.

    시스템 내 역할:
        chat_json()에서 fallback_payload로 전달되어, LLM 호출 실패 시
        _build_error_payload()가 이 값을 기반으로 에러 응답을 구성한다.
        이를 통해 분류 파이프라인이 절대 None을 반환하지 않도록 보장한다.

    Args:
        action: 폴백 시 수행할 액션. 기본값은 "clarify"로, 사용자에게 재질문한다.

    Returns:
        모든 예약 슬롯이 비어 있는 안전한 분류 결과 딕셔너리.
    """
    return {
        "action": action,
        "department": None,
        "doctor_name": None,
        "date": None,
        "time": None,
        "customer_type": None,
        "is_first_visit": None,
        "patient_name": None,
        "patient_contact": None,
        "birth_date": None,
        "is_proxy_booking": False,
        "is_emergency": False,
        "symptom_keywords": [],
        "missing_info": [],
        "target_appointment_hint": None,
    }


def build_safety_fallback() -> dict:
    """
    안전성 검사(safety check) LLM 호출이 실패했을 때 사용할 안전한 기본 페이로드를 생성한다.

    목적:
        safety gate LLM이 응답하지 못했을 때, 시스템이 의료 상담 우회나
        긴급 상황 미감지 없이 안전하게 계속 동작할 수 있도록 기본값을 제공한다.

    동작 흐름:
        모든 안전성 플래그(is_medical, is_off_topic, is_emergency)를 False로
        설정한 딕셔너리를 반환한다. 이는 "위험 요소 없음"을 의미하며,
        일반 예약 흐름을 계속 진행하도록 허용한다.

    시스템 내 역할:
        safety 분류 파이프라인에서 chat_json()의 fallback_payload로 전달된다.
        LLM이 응답하지 못해도 챗봇이 중단되지 않고,
        보수적으로 "안전한 입력"으로 간주하여 흐름을 이어간다.

    Returns:
        모든 안전성 플래그가 False인 딕셔너리.
    """
    return {
        "is_medical": False,
        "is_off_topic": False,
        "is_emergency": False,
    }


def _strip_code_fences(raw_content: str) -> str:
    """
    LLM 응답에서 마크다운 코드 펜스(```)를 제거한다.

    목적:
        일부 LLM 모델은 JSON 응답을 ```json ... ``` 형태의 코드 블록으로
        감싸서 반환하는 경우가 있다. 이 함수는 코드 펜스를 제거하여
        순수한 JSON 문자열만 남긴다.

    동작 흐름:
        1. 입력 문자열의 앞뒤 공백을 제거한다.
        2. 문자열이 ```로 시작하면 코드 펜스로 판단한다.
        3. 첫 번째 줄(```json 등)과 마지막 줄(```)을 제거한다.
        4. 나머지 줄들을 다시 합쳐서 반환한다.

    시스템 내 역할:
        safe_parse_json()에서 JSON 파싱 전에 호출되어,
        코드 펜스가 포함된 응답도 정상적으로 파싱될 수 있도록 전처리한다.

    Args:
        raw_content: LLM이 반환한 원본 텍스트.

    Returns:
        코드 펜스가 제거된 순수 JSON 문자열.
    """
    content = str(raw_content).strip()
    if content.startswith("```"):
        lines = content.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines).strip()
    return content


def safe_parse_json(raw_content: str) -> dict | None:
    """
    LLM 응답 문자열을 안전하게 JSON 딕셔너리로 파싱한다.

    목적:
        LLM의 원본 텍스트 응답을 파이썬 딕셔너리로 변환한다.
        코드 펜스 제거 후 파싱하며, 결과가 dict가 아니면 None을 반환한다.

    동작 흐름:
        1. raw_content가 None이면 즉시 None을 반환한다.
        2. _strip_code_fences()로 코드 펜스를 제거한다.
        3. json.loads()로 JSON 파싱을 시도한다.
        4. 파싱 결과가 dict이면 반환, 그렇지 않으면(리스트, 문자열 등) None을 반환한다.
        5. json.JSONDecodeError나 TypeError는 호출자(chat_json)에서 처리한다.

    시스템 내 역할:
        chat_json()에서 Ollama 응답을 파싱할 때 사용되는 핵심 유틸리티.
        파싱 실패 시 chat_json()이 재시도 또는 fallback을 수행한다.

    Args:
        raw_content: LLM이 반환한 원본 텍스트 (코드 펜스 포함 가능).

    Returns:
        파싱된 딕셔너리, 또는 파싱 불가 시 None.
    """
    if raw_content is None:
        return None

    parsed = json.loads(_strip_code_fences(raw_content))
    if isinstance(parsed, dict):
        return parsed
    return None


def _default_fallback_action(error_code: str) -> str:
    """
    에러 코드에 따라 기본 폴백 액션 문자열을 결정한다.

    목적:
        LLM 호출 실패 시, 에러의 종류에 따라 시스템이 취할 기본 행동을 결정한다.
        일시적 오류(연결 거부, 타임아웃, JSON 파싱 실패)는 "clarify"(재질문),
        그 외 알 수 없는 오류는 "reject"(거부)로 처리한다.

    동작 흐름:
        1. error_code가 일시적 오류 집합에 포함되면 "clarify"를 반환한다.
        2. 그 외의 경우 "reject"를 반환한다.

    시스템 내 역할:
        _build_error_payload()에서 호출되어, 에러 페이로드의
        _fallback_action 필드 값을 결정한다.
        "clarify"는 사용자에게 다시 말해달라고 요청하는 안전한 응답이고,
        "reject"는 처리를 거부하는 더 보수적인 응답이다.

    Args:
        error_code: 오류 유형을 나타내는 문자열 (예: "ollama_connection_refused").

    Returns:
        "clarify" 또는 "reject" 중 하나.
    """
    if error_code in {
        "ollama_connection_refused",
        "ollama_timeout",
        "json_parse_failed",
        "ollama_response_invalid",
    }:
        return "clarify"
    return "reject"


def _build_error_payload(error_code: str, fallback_payload: dict | None = None, **extra) -> dict:
    """
    LLM 호출 실패 시 반환할 에러 페이로드 딕셔너리를 구성한다.

    목적:
        LLM 오류 발생 시, 호출자가 기대하는 딕셔너리 형태를 유지하면서
        에러 정보(_error, _fallback_action, _fallback_message)를 포함한
        안전한 응답을 만든다. 이를 통해 상위 모듈이 에러를 감지하고
        적절한 사용자 메시지를 생성할 수 있다.

    동작 흐름:
        1. fallback_payload가 있으면 그 내용을 복사하여 기반 딕셔너리로 사용한다.
        2. _error (에러 코드), _fallback_action (기본 행동),
           _fallback_message (사용자에게 보여줄 메시지)를 추가한다.
        3. extra 키워드 인자들(_raw, _message, _retries 등)을 추가하여
           디버깅 정보를 포함시킨다.

    시스템 내 역할:
        chat_json()의 모든 에러 경로에서 호출되는 중앙 에러 페이로드 생성기.
        fallback_payload 덕분에 분류/안전성 검사 각각의 기본 스키마를 유지하면서
        에러 메타데이터를 덧붙일 수 있다.

    Args:
        error_code: 오류 유형 문자열 (예: "ollama_timeout").
        fallback_payload: 기반이 될 폴백 딕셔너리 (예: build_classification_fallback() 결과).
        **extra: 추가 디버깅 정보 (예: _raw, _message, _retries).

    Returns:
        에러 메타데이터가 포함된 안전한 폴백 딕셔너리.
    """
    payload = dict(fallback_payload or {})
    payload.update({
        "_error": error_code,
        "_fallback_action": _default_fallback_action(error_code),
        "_fallback_message": TEMPORARY_ERROR_MESSAGE,
    })
    payload.update(extra)
    return payload


def _normalize_exception_code(exc: Exception) -> str:
    """
    파이썬 예외 객체를 시스템 내부 에러 코드 문자열로 정규화한다.

    목적:
        다양한 형태로 발생할 수 있는 Ollama 관련 예외들을
        통일된 에러 코드 문자열로 변환한다. 이를 통해 에러 처리 로직이
        예외 타입에 구애받지 않고 일관된 분기를 수행할 수 있다.

    동작 흐름:
        1. 먼저 예외의 타입(isinstance)으로 판별한다:
           - ConnectionRefusedError → "ollama_connection_refused"
           - TimeoutError, socket.timeout → "ollama_timeout"
        2. 타입으로 판별 불가 시, 예외 메시지 문자열을 소문자로 변환하여
           키워드 매칭을 수행한다:
           - "connection refused" 등 → "ollama_connection_refused"
           - "timeout" 등 → "ollama_timeout"
        3. 어디에도 해당하지 않으면 범용 코드 "ollama_call_failed"를 반환한다.

    시스템 내 역할:
        chat_json()에서 Ollama 호출 중 예외 발생 시 호출되어,
        _build_error_payload()에 전달할 에러 코드를 결정한다.
        문자열 매칭을 통해 래핑된 예외도 올바르게 분류할 수 있다.

    Args:
        exc: Ollama 호출 중 발생한 예외 객체.

    Returns:
        정규화된 에러 코드 문자열.
    """
    if isinstance(exc, ConnectionRefusedError):
        return "ollama_connection_refused"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "ollama_timeout"

    error_text = str(exc).lower()
    if "connection refused" in error_text or "failed to connect" in error_text or "connection failed" in error_text:
        return "ollama_connection_refused"
    if "timeout" in error_text or "timed out" in error_text:
        return "ollama_timeout"

    return "ollama_call_failed"


def chat_json(
    messages: list[dict],
    chat_fn=None,
    max_parse_retries: int = 1,
    fallback_payload: dict | None = None,
) -> dict:
    """
    Ollama LLM에 JSON 형식으로 요청을 보내고, 파싱된 딕셔너리를 반환한다.

    목적:
        이 모듈의 핵심 함수. Ollama API에 format='json' 옵션으로 호출하여
        구조화된 JSON 응답을 받고, 이를 파이썬 딕셔너리로 변환하여 반환한다.
        어떤 오류가 발생하더라도 예외를 전파하지 않고 안전한 fallback을 반환한다.

    동작 흐름:
        1. chat_fn이 None이면 ollama.chat를 기본값으로 사용한다.
           (테스트 시 mock 함수를 주입할 수 있도록 DI 패턴 적용)
        2. while 루프 내에서 Ollama API를 호출한다.
        3. API 호출 자체가 실패하면 (연결 거부, 타임아웃 등)
           _normalize_exception_code()로 에러 코드를 결정하고 에러 페이로드를 반환한다.
        4. 응답에서 message.content를 추출한다. 구조가 잘못되면 에러 페이로드를 반환한다.
        5. safe_parse_json()으로 JSON 파싱을 시도한다.
           - JSONDecodeError 발생 시 max_parse_retries만큼 재시도(continue)한다.
           - 재시도 초과 시 에러 페이로드를 반환한다.
           - TypeError 발생 시 즉시 에러 페이로드를 반환한다.
        6. 파싱 결과가 None이면 (dict가 아닌 경우) 에러 페이로드를 반환한다.
        7. 정상 파싱된 딕셔너리를 반환한다.

    시스템 내 역할:
        분류(classification) 파이프라인과 안전성(safety) 검사 파이프라인 모두에서
        LLM을 호출할 때 이 함수를 사용한다. fallback_payload 파라미터를 통해
        각 파이프라인에 맞는 안전한 기본값(build_classification_fallback 또는
        build_safety_fallback)을 지정할 수 있다.

    Args:
        messages: Ollama에 전달할 대화 메시지 리스트 (OpenAI 호환 형식).
        chat_fn: Ollama chat 함수. None이면 ollama.chat 사용 (테스트 시 mock 주입용).
        max_parse_retries: JSON 파싱 실패 시 재시도 횟수. 기본값 1.
        fallback_payload: LLM 실패 시 기반이 될 폴백 딕셔너리.

    Returns:
        LLM 응답을 파싱한 딕셔너리, 또는 오류 시 에러 메타데이터가 포함된 폴백 딕셔너리.
    """
    if chat_fn is None:
        chat_fn = ollama.chat

    parse_attempt = 0
    while True:
        try:
            response = chat_fn(
                model=MODEL_NAME,
                messages=messages,
                format="json",
            )
        except Exception as exc:
            return _build_error_payload(
                _normalize_exception_code(exc),
                fallback_payload=fallback_payload,
                _message=str(exc),
            )

        try:
            raw_content = response["message"]["content"]
        except (TypeError, KeyError):
            return _build_error_payload(
                "ollama_response_invalid",
                fallback_payload=fallback_payload,
                _raw=response,
            )

        try:
            parsed = safe_parse_json(raw_content)
        except json.JSONDecodeError:
            if parse_attempt < max_parse_retries:
                parse_attempt += 1
                continue
            return _build_error_payload(
                "json_parse_failed",
                fallback_payload=fallback_payload,
                _raw=raw_content,
                _retries=parse_attempt,
            )
        except TypeError:
            return _build_error_payload(
                "json_parse_failed",
                fallback_payload=fallback_payload,
                _raw=raw_content,
                _retries=parse_attempt,
            )

        if parsed is None:
            return _build_error_payload(
                "ollama_response_invalid",
                fallback_payload=fallback_payload,
                _raw=raw_content,
            )

        return parsed


def chat_text(messages: list[dict], chat_fn=None) -> str:
    """
    Ollama LLM에 일반 텍스트 형식으로 요청을 보내고, 응답 문자열을 반환한다.

    목적:
        JSON 구조가 아닌 자연어 텍스트 응답이 필요한 경우에 사용한다.
        예를 들어, 사용자에게 보여줄 최종 안내 메시지를 LLM이 생성할 때 사용된다.

    동작 흐름:
        1. chat_fn이 None이면 ollama.chat를 기본값으로 사용한다.
        2. format 옵션 없이 Ollama API를 호출한다 (자유 형식 텍스트 응답).
        3. 호출 성공 시 response["message"]["content"]를 반환한다.
        4. 어떤 예외가 발생하더라도 TEMPORARY_ERROR_MESSAGE를 반환하여
           사용자에게 일시적 오류 안내를 보여준다.

    시스템 내 역할:
        예약 챗봇에서 사용자에게 보여줄 자연어 응답(안내 문구, 확인 메시지 등)을
        생성할 때 사용된다. chat_json()과 달리 구조화된 데이터가 아닌
        사람이 읽을 수 있는 텍스트를 반환한다.

    Args:
        messages: Ollama에 전달할 대화 메시지 리스트 (OpenAI 호환 형식).
        chat_fn: Ollama chat 함수. None이면 ollama.chat 사용 (테스트 시 mock 주입용).

    Returns:
        LLM의 텍스트 응답 문자열, 또는 오류 시 사용자용 에러 메시지.
    """
    if chat_fn is None:
        chat_fn = ollama.chat

    try:
        response = chat_fn(
            model=MODEL_NAME,
            messages=messages,
        )
    except Exception:
        return TEMPORARY_ERROR_MESSAGE

    return response.get("message", {}).get("content", "")
