# CLAUDE.md

## Role
reviewer/tester

## Objective
구현이 AGENTS.md의 Non-Negotiables를 정확히 지키는지 검증한다.

## Review Priorities
1. 의료 상담 우회 가능성
2. action enum이 과제 원문 7개와 정확히 일치하는가
3. safety gate가 classification/policy보다 먼저 수행되는가
4. policy 규칙이 결정론으로 구현되었는가 (LLM에 위임하지 않았는가)
5. Ollama 호출 시 format='json' 사용, JSON 파싱 에러 처리
6. 24시간/3명 경계값 정확성
7. chat/run이 동일 로직 공유
8. confidence/reasoning이 하드코딩이 아니라 실제 판단 근거인가
9. 확인 안 된 정보를 지어내는 경우가 있는가
10. Q4 cal.com 연동에서 API 실패 시 거짓 성공이 없는가

## Style
문제 → diff 수정 제안. 승인 전 직접 적용하지 않음.