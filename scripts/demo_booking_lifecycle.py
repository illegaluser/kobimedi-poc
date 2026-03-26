#!/usr/bin/env python3
"""
scripts/demo_booking_lifecycle.py — 예약 생명주기 대화형 데모

chat.py를 실제로 실행하여 사람이 타이핑하는 것처럼
진료예약 → 예약변경 → 예약취소 전체 플로우를 시연한다.

사용법:
  python scripts/demo_booking_lifecycle.py              # 기본 속도
  python scripts/demo_booking_lifecycle.py --fast       # 빠른 모드 (데모 녹화용)
  python scripts/demo_booking_lifecycle.py --slow       # 느린 모드 (프레젠테이션용)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── 타이핑 속도 설정 ──
SPEED = {
    "fast": {"char": 0.03, "pause": 0.5, "response_wait": 0.5, "phase_pause": 1.5},
    "normal": {"char": 0.06, "pause": 1.0, "response_wait": 1.0, "phase_pause": 2.5},
    "slow": {"char": 0.10, "pause": 1.5, "response_wait": 1.5, "phase_pause": 3.5},
}

# ── ANSI 색상 ──
CYAN = "\033[1;36m"
YELLOW = "\033[1;33m"
DIM = "\033[2m"
RESET = "\033[0m"

# ── 대화 시나리오 ──
SCENARIO = [
    ("Phase 1: 진료예약", [
        "4월 14일 오전 10시에 내과 진료 예약하고 싶어요",
        "본인이에요",
        "홍길동 010-9876-5432",
        "네",
    ]),
    ("Phase 2: 예약변경", [
        "예약 변경할래요",
        "본인",
        "홍길동 010-9876-5432",
        "4월 14일 오전 11시로 변경해주세요",
    ]),
    ("Phase 3: 예약취소", [
        "예약 취소해주세요",
        "본인",
        "홍길동 010-9876-5432",
    ]),
]


def type_out(text: str, speed: dict):
    """한 글자씩 타이핑하는 효과."""
    for char in text:
        sys.stdout.write(char)
        sys.stdout.flush()
        time.sleep(speed["char"])


def print_phase(label: str):
    print()
    print(f"{CYAN}{'─' * 50}{RESET}")
    print(f"{CYAN}  {label}{RESET}")
    print(f"{CYAN}{'─' * 50}{RESET}")
    print()


def run_demo(speed_name: str = "normal"):
    speed = SPEED[speed_name]

    proc = subprocess.Popen(
        [sys.executable, "-u", "chat.py"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(PROJECT_ROOT),
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        text=True,
        bufsize=1,
    )

    def read_response() -> str:
        """챗봇 응답을 한 줄씩 읽는다. '> ' 프롬프트가 나올 때까지."""
        lines = []
        while True:
            line = proc.stdout.readline()
            if not line:
                break
            stripped = line.rstrip("\n")
            if stripped == "> " or line == "> ":
                break
            if stripped.endswith("> "):
                lines.append(stripped[:-2].rstrip())
                break
            lines.append(stripped)
        return "\n".join(lines)

    def send(msg: str):
        proc.stdin.write(msg + "\n")
        proc.stdin.flush()

    try:
        greeting = read_response()
        print(f"{greeting}")
        time.sleep(speed["response_wait"])

        for phase_label, messages in SCENARIO:
            print_phase(phase_label)
            time.sleep(speed["phase_pause"])

            for msg in messages:
                sys.stdout.write(f"{DIM}> {RESET}")
                sys.stdout.flush()
                type_out(msg, speed)
                print()
                time.sleep(speed["pause"])

                send(msg)

                response = read_response()
                if response:
                    print(f"{response}")
                time.sleep(speed["response_wait"])

        print_phase("데모 완료")
        send("exit")

    except KeyboardInterrupt:
        print(f"\n\n{YELLOW}데모 중단됨{RESET}")
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def main():
    parser = argparse.ArgumentParser(description="예약 생명주기 대화형 데모")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--fast", action="store_true", help="빠른 모드")
    group.add_argument("--slow", action="store_true", help="느린 모드")
    args = parser.parse_args()

    speed = "fast" if args.fast else "slow" if args.slow else "normal"

    print(f"{YELLOW}╔══════════════════════════════════════════════════╗{RESET}")
    print(f"{YELLOW}║   코비메디 예약 생명주기 데모                    ║{RESET}")
    print(f"{YELLOW}║   진료예약 → 예약변경 → 예약취소                ║{RESET}")
    print(f"{YELLOW}║   속도: {speed:<42s}║{RESET}")
    print(f"{YELLOW}╚══════════════════════════════════════════════════╝{RESET}")
    print()

    run_demo(speed)


if __name__ == "__main__":
    main()
