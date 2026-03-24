from __future__ import annotations

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
        print(result.get("response", "오류가 발생했습니다. 다시 시도해 주세요."))


if __name__ == "__main__":
    main()