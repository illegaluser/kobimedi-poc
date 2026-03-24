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
6. 확인되지 않은 정보를 지어내지 않는다.
7. Ollama(qwen3-coder:30b)로 LLM 호출. 구조화 출력은 format='json'.
8. Q4 cal.com 연동은 src/calcom_client.py를 통해 공통 로직으로 처리한다.

## Pipeline Order
1. safety gate → 2. classification → 3. extraction →
4. policy check → 5. (Q4) cal.com → 6. response build

## Priority Order
safety > correctness > policy compliance > demo polish > Q4

## Forbidden
- 의료 판단 생성
- action enum 축약
- chat/run 별도 로직
- 확인 안 된 정책/가용시간 지어내기
- confidence/reasoning 하드코딩

## Required Reads
- .ai/handoff/00_request.md
- docs/policy_digest.md
- .ai/harness/features.json
- .ai/harness/progress.md