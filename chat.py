from __future__ import annotations

import sys
sys.dont_write_bytecode = True

from src.agent import create_session, process_message


def main() -> None:
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