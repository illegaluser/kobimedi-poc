# AGENTS.md

## Mission
코비메디 예약 Agent PoC를 과제 요구사항에 맞게 구현한다.

## Non-Negotiables
1. chat.py와 run.py는 동일한 src/agent.py 로직을 공유한다.
2. action enum은 과제 원문 7개 값을 그대로 사용한다:
   book_appointment, modify_appointment, cancel_appointment,
   check_appointment, clarify, escalate, reject
3. 의료 상담/목적 외 사용/인젝션은 안전 게이트에서 먼저 차단한다.
4. 정책 규칙은 policy.py에서 결정론으로 우선 처리한다.
5. 결과 JSON 키는 과제 예시에 맞춘다.
6. Ollama(qwen3-coder:30b)로 LLM 호출. 구조화 출력은 format='json'.

## Priority Order
safety > correctness > policy compliance > demo polish > optional features

## Forbidden
- 의료 판단 생성
- action enum 축약 (book 대신 book_appointment)
- chat/run 별도 로직 구현
- 모르는 정책을 지어내기

## Required Reads
- .ai/handoff/00_request.md
- docs/policy_digest.md
- .ai/harness/features.json
- .ai/harness/progress.md