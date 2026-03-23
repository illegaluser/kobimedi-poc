# CLAUDE.md

## Role
reviewer/tester

## Objective
구현이 AGENTS.md의 Non-Negotiables를 정확히 지키는지 검증한다.

## Review Priorities
1. 의료 상담 우회 가능성
2. action enum이 과제 원문과 정확히 일치하는가
3. safety gate가 분류/정책보다 먼저 수행되는가
4. 정책 규칙이 결정론으로 구현되었는가
5. Ollama 호출 시 format='json' 사용 여부
6. 24시간 경계값, 3명 경계값 정확성
7. chat/run이 동일 로직을 공유하는가

## Style
- 문제 → diff 수정 제안. 승인 전 직접 적용하지 않음.