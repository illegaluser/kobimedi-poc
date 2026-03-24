import json
import socket

import ollama


MODEL_NAME = "qwen3-coder:30b"
TEMPORARY_ERROR_MESSAGE = "일시적 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."


def _strip_code_fences(raw_content: str) -> str:
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
    if raw_content is None:
        return None

    parsed = json.loads(_strip_code_fences(raw_content))
    if isinstance(parsed, dict):
        return parsed
    return None


def _default_fallback_action(error_code: str) -> str:
    if error_code in {"ollama_connection_refused", "ollama_timeout", "json_parse_failed"}:
        return "clarify"
    return "reject"


def _build_error_payload(error_code: str, **extra) -> dict:
    payload = {
        "_error": error_code,
        "_fallback_action": _default_fallback_action(error_code),
        "_fallback_message": TEMPORARY_ERROR_MESSAGE,
    }
    payload.update(extra)
    return payload


def _normalize_exception_code(exc: Exception) -> str:
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


def chat_json(messages: list[dict], chat_fn=None, max_parse_retries: int = 1) -> dict:
    """Call Ollama with format='json' and return parsed JSON or a safe fallback payload."""
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
                _message=str(exc),
            )

        try:
            raw_content = response["message"]["content"]
        except (TypeError, KeyError):
            return _build_error_payload(
                "ollama_response_invalid",
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
                _raw=raw_content,
                _retries=parse_attempt,
            )
        except TypeError:
            return _build_error_payload(
                "json_parse_failed",
                _raw=raw_content,
                _retries=parse_attempt,
            )

        if parsed is None:
            return _build_error_payload(
                "ollama_response_invalid",
                _raw=raw_content,
            )

        return parsed


def chat_text(messages: list[dict], chat_fn=None) -> str:
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