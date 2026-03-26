# Progress

## 2026-03-26: 채팅 플로우 버그 수정 + 시나리오 테스트 체계 구축

### 버그 수정 (8건)

| 커밋 | 문제 | 수정 |
|------|------|------|
| `f9fa2b1` | Cal.com 환자이름이 "다음주"로 저장 | `_NON_NAME_WORDS`에 시간 키워드 추가 + 확정된 이름 덮어쓰기 방지 |
| `ce9d279` | 예약 변경 시 "바꿔줘" off-topic 차단, "4월1일" 미인식, 새 날짜/시간 미수집 | `_is_booking_related`에 구어체 추가, 한글 날짜 파서 추가, modify missing_info 추가 |
| `7ad91dd` | 취소 구어체("빼줘","안 갈래") off-topic 차단 | 3곳에 cancel 구어체 키워드 추가 |
| `7001252` | LLM이 기존 date/time 추출 시 변경 완료 처리 | `target_appointment` 대비 비교로 기존값=미입력 판정 |
| `f8e77c7` | 초진 환자에게 17:30(40분 초과) 슬롯 안내 | 슬롯 안내 시 초진/재진 진료시간 반영 필터링 |
| `c1c8d94` | customer_name=None일 때 이름 미수집 | 실제 이름 존재 시에만 질문 스킵 |
| `1643328` | "필요해"/"죽을것" 등이 환자이름으로 오추출 | `_NON_NAME_WORDS` + 동사 활용형 패턴 보강 |
| `19bbf66` | LLM escalate/reject 시 response=None | policy 호출 전 early return + 안내 메시지 추가 |
| 이번 커밋 | 예약 진행 중 LLM이 맥락 없이 escalate 반환 | pending_action이 booking 관련이면 escalate/reject 무시하고 복원 |
| 이번 커밋 | 초진/재진 진료시간 사전 안내 없음 | 시간 선택 질문에 "초진 40분/재진 30분" 안내 추가 |
| 이번 커밋 | 예약 취소 시 Cal.com 원격 취소 미실행 | cancel_booking(로컬) + cancel_booking_remote(Cal.com) 호출 추가 |
| 이번 커밋 | Cal.com 예약 UID가 로컬에 미저장 | 예약 생성 시 calcom_uid를 appointment에 저장 |
| `dd96589` 이후 | 예약 변경 시 로컬/Cal.com 모두 미반영 | 기존 취소(로컬+Cal.com) + 신규 생성(로컬+Cal.com) 구현 |
| 이번 커밋 | 예약 확인/변경/취소 시 Cal.com 예약 조회 불가 | _find_customer_appointments에 Cal.com list_bookings 폴백 추가 |
| 이번 커밋 | Cal.com 연동 E2E 검증 스크립트 없음 | verify_calcom_lifecycle.py: 예약→Cal.com확인→변경→확인→취소→확인 6단계 검증 |

### 신규 스크립트

| 파일 | 용도 |
|------|------|
| `scripts/test_booking_lifecycle.py` | 예약→변경→취소 생명주기 통합 테스트 (실제 Ollama + Cal.com) |
| `scripts/demo_booking_lifecycle.py` | chat.py를 사람이 타이핑하듯 시연하는 대화형 데모 |

---

## 2026-03-26 (earlier): F-052 운영시간 정책 구현 + 시나리오 테스트 체계 구축

### 구현 사항

#### src/policy.py — 운영시간 검증 로직 추가 (F-052)

- `is_within_operating_hours(request_start, request_end)` 함수 신규 추가
  - 평일(월~금): 09:00-18:00
  - 토요일: 09:00-13:00
  - 일요일: 휴진 (예약 불가)
  - 점심시간: 12:30-13:30 (예약 불가)
- `is_slot_available()`에 운영시간 검증 통합 — 기존 정원/겹침 검사 전에 실행
- `suggest_alternative_slots()`에 토요일 13:00 종료 반영

#### src/calcom_client.py — 예약 조회/취소 API 추가

- `list_bookings()`: GET /bookings 전체 예약 조회
- `cancel_booking_remote()`: POST /bookings/{uid}/cancel 원격 취소

#### tests/test_scenarios.py — 유닛 테스트 61개 (10개 카테고리)

- Category 1: 정상 예약 완료 (4개)
- Category 2: 환자 식별 & 대리 예약 (4개)
- Category 3: 정책 엔진 슬롯 계산 (5개)
- Category 4: 24시간 변경/취소 규칙 (5개)
- Category 5: Safety Gate (7개)
- Category 6: 분과 및 운영시간 (3개)
- Category 7: 운영시간 정책 F-052 (12개)
- Category 8: 대화 상태 관리 (3개)
- Category 9: Q4 Cal.com 외부 연동 (8개)
- Category 10: 예약→변경→취소 전체 플로우 (10개)

#### scripts/ — 운영 스크립트

- `run_scenario_tests.py`: 10개 카테고리 시나리오를 실제 Ollama + Cal.com으로 실행하는 러너
- `test_booking_lifecycle.py`: 예약→변경→취소 생명주기 통합 테스트 (Category 10에서 호출)
- `run_tests.sh`: 유닛 + 시나리오 통합 실행기 (`--scenario`, `--all`)
- `cleanup_bookings.py`: Cal.com 예약 일괄 삭제 + 로컬 bookings.json 동기화

### 테스트 결과

- 유닛 테스트: 236 passed, 0 failed (~18초)
- 시나리오 테스트: 10개 카테고리 ALL PASSED
