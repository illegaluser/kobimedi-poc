import json

import ollama


MODEL_NAME = "qwen3-coder:30b"


def safe_parse_json(raw_content: str) -> dict:
    return json.loads(raw_content)


def chat_json(messages: list[dict], chat_fn=None) -> dict:
    if chat_fn is None:
        chat_fn = ollama.chat

    response = chat_fn(
        model=MODEL_NAME,
        messages=messages,
        format="json",
    )
    return safe_parse_json(response["message"]["content"])


def chat_text(messages: list[dict], chat_fn=None) -> str:
    if chat_fn is None:
        chat_fn = ollama.chat

    response = chat_fn(
        model=MODEL_NAME,
        messages=messages,
    )
    return response["message"]["content"]