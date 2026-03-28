"""
코비메디 예약 챗봇 — 대화형(멀티턴) CLI 인터페이스.

터미널에서 사용자와 한 턴씩 대화하며 예약·변경·취소 등을 처리한다.
세션 상태(대화 이력, 환자 정보 등)는 create_session()이 관리한다.

사용법:
    python chat.py
    > 내일 2시 내과 예약하고 싶어요
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

from src.agent import create_session, process_message


def main() -> None:
    """대화형 챗봇 REPL을 실행한다. quit/exit 또는 Ctrl+C로 종료."""
    print("🏥 코비메디 예약 챗봇입니다. 무엇을 도와드릴까요?")
    session = create_session()

    while True:
        try:
            user_message = input("> ").strip()
        except EOFError:
            print("안녕히 가세요.")
            break
        except KeyboardInterrupt:
            print("\n안녕히 가세요.")
            break

        if user_message.lower() in {"quit", "exit"}:
            print("안녕히 가세요.")
            break

        if not user_message:
            continue

        result = process_message(user_message, session)
        response = result.get("response") if result else None
        print(response or "죄송합니다. 말씀을 이해하지 못했어요. 다시 말씀해 주시겠어요?")


if __name__ == "__main__":
    main()