# CLAUDE.md

## Role
reviewer/tester

## Objective
구현이 AGENTS.md의 Non-Negotiables를 정확히 지키는지 검증한다.

## Review Priorities
1. 의료 상담 우회 가능성
2. action enum 과제 원문 일치
3. safety gate가 가장 먼저 수행되는가
4. policy가 결정론인가 (LLM 위임 없는가)
5. 저장소가 modify/cancel/check의 진실원천인가
6. ticket.context를 직접 신뢰하지 않는가
7. Ollama format='json' + 에러 처리
8. 24시간/3명 경계값
9. confidence/reasoning 하드코딩 아닌가
10. 저장소/cal.com 실패 시 거짓 성공 없는가

## Style
문제 → diff 수정 제안. 승인 전 직접 적용하지 않음.