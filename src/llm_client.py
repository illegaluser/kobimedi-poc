import json

import ollama


MODEL_NAME = "qwen3-coder:30b"


def safe_parse_json(raw_content: str) -> dict | None:
    try:
        return json.loads(raw_content)
    except (TypeError, json.JSONDecodeError):
        return None


def chat_json(messages: list[dict], chat_fn=None) -> dict:
    """Call Ollama with format='json' and return parsed JSON or an error payload."""
    if chat_fn is None:
        chat_fn = ollama.chat

    try:
        response = chat_fn(
            model=MODEL_NAME,
            messages=messages,
            format="json",
        )
    except Exception as exc:
        return {
            "_error": "ollama_call_failed",
            "_message": str(exc),
        }

    try:
        raw_content = response["message"]["content"]
    except (TypeError, KeyError):
        return {
            "_error": "ollama_response_invalid",
            "_raw": response,
        }

    parsed = safe_parse_json(raw_content)
    if parsed is None:
        return {
            "_error": "json_parse_failed",
            "_raw": raw_content,
        }

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
        return ""

    return response.get("message", {}).get("content", "")