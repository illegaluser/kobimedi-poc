# Progress

## 2026-03-26: F-052 운영시간 정책 구현 + 시나리오 테스트 51개 + E2E 테스트 22개

### 구현 사항

#### src/policy.py — 운영시간 검증 로직 추가 (F-052)

- `is_within_operating_hours(request_start, request_end)` 함수 신규 추가
  - 평일(월~금): 09:00-18:00
  - 토요일: 09:00-13:00
  - 일요일: 휴진 (예약 불가)
  - 점심시간: 12:30-13:30 (예약 불가)
- `is_slot_available()`에 운영시간 검증 통합 — 기존 정원/겹침 검사 전에 실행
- `suggest_alternative_slots()`에 토요일 13:00 종료 반영

#### tests/test_scenarios.py — Mock 기반 시나리오 테스트 51개 (9개 카테고리)

- Category 1: 정상 예약 완료 (4개)
- Category 2: 환자 식별 & 대리 예약 (4개)
- Category 3: 정책 엔진 슬롯 계산 (5개)
- Category 4: 24시간 변경/취소 규칙 (5개)
- Category 5: Safety Gate (7개)
- Category 6: 분과 및 운영시간 (3개)
- Category 7: 운영시간 정책 F-052 (12개)
- Category 8: 대화 상태 관리 (3개)
- Category 9: Q4 Cal.com 외부 연동 (8개)

#### tests/test_e2e.py — 완전 E2E 테스트 22개 (Mock 없음)

- 실제 Ollama(qwen3-coder:30b) + 실제 Storage + 실제 Cal.com API
- LLM 비결정론 대응: action enum + 핵심 상태만 검증
- `pytest.mark.e2e` 마커로 일반 테스트와 분리
- `pytest.ini`에서 기본 실행 시 E2E 제외 (`addopts = -m "not e2e"`)

### 테스트 결과

- Mock 기반: 214 passed, 0 failed, 22 deselected (6.45s)
- E2E: 22 passed, 0 failed (53.48s)
- 기존 테스트 회귀 없음
