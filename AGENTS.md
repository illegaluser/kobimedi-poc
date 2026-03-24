# AGENTS.md

## Mission
코비메디 예약 Agent PoC를 과제 요구사항에 맞게 구현한다.

## Non-Negotiables
1. chat.py와 run.py는 동일한 src/agent.py 로직을 공유한다.
2. action enum은 과제 원문 7개 값을 그대로 사용한다.
3. 의료 상담/목적 외 사용/인젝션은 안전 게이트에서 먼저 차단한다.
4. 정책 규칙은 policy.py에서 결정론으로 처리한다.
5. 예약 상태는 파일 기반 영속 저장소(data/bookings.json)를 진실원천으로 사용한다.
6. ticket.context는 보조 힌트일 뿐, 저장소가 최종 판정 기준이다.
7. 확인되지 않은 정보를 지어내지 않는다.
8. Ollama(qwen3-coder:30b)로 LLM 호출. 구조화 출력은 format='json'.
9. confidence/reasoning을 하드코딩하지 않는다.
10. Q4 cal.com 연동은 src/calcom_client.py를 통해 공통 로직으로 처리한다.

## Pipeline Order
1. safety gate → 2. classification → 3. extraction →
4. dialogue state merge → 5. storage lookup →
6. policy check → 7. (Q4) cal.com → 8. persist → 9. response build

## Priority Order
safety > correctness > policy compliance > demo polish > Q4

## Forbidden
- 의료 판단 생성
- action enum 축약
- chat/run 별도 로직
- 확인 안 된 정보 생성
- confidence/reasoning 하드코딩
- 저장소 실패 시 거짓 성공

## Required Reads
- .ai/handoff/00_request.md
- docs/policy_digest.md
- .ai/harness/features.json
- .ai/harness/progress.md