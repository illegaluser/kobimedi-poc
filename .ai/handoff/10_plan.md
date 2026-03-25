# 구현 실행 계획 (Implementation Plan)

기준 문서: `docs/policy_digest.md`, `.ai/harness/features.json`  
최종 업데이트: 2026-03-25

---

## 1. 문제 정의

코비메디 예약 Agent는 다음 세 가지 핵심 문제를 해결해야 한다.

1. **환자 신원 확인 문제**: `tickets.json`에는 성명(`customer_name`)은 있으나 전화번호가 없다. 성명만으로는 동명이인 식별이 불가하고, 초진/재진 판정의 진실원천으로 사용할 수 없다.

2. **본인/대리인 구분 문제**: 성명과 전화번호를 제공하더라도, 예약을 진행하는 사람이 **환자 본인인지 대리인인지를 성명/전화번호만으로는 알 수 없다.** 이를 구분하지 않으면 대리 예약 시 잘못된 환자 정보로 예약이 진행될 수 있다.

3. **멀티턴 상태 관리 문제**: clarify는 단발 응답이 아니라 누적 상태머신이어야 하며, 본인/대리인 확인 → 환자 정보 수집 → 예약 정보 수집 → 정책 검사 → 확인 → 영속화의 흐름이 안정적으로 작동해야 한다.

---

## 2. 설계 원칙 요약

| 원칙 | 내용 |
| --- | --- |
| **safety first** | 모든 요청은 safety gate를 먼저 통과해야 한다 |
| **본인/대리인 먼저** | 예약 의도 확정 직후, 환자 정보 수집 전에 본인/대리인 여부를 묻는다 |
| **전화번호 우선 식별** | 환자 식별: ① 전화번호 → ② 이름+생년월일 → ③ 이름 단독(유일할 때) |
| **저장소 진실원천** | 초진/재진 판정은 `data/bookings.json`에서 결정. ticket.context는 힌트만 |
| **결정론 정책** | 24시간/정원/운영시간/슬롯 계산은 `src/policy.py`에서 LLM 없이 처리 |
| **tickets.json 불변** | 입력 스키마를 변경하지 않는다. 추가 정보는 대화에서 수집하거나 저장소에서 조회 |
| **거짓 성공 금지** | 저장 실패, LLM 오류, cal.com 실패 시 성공 응답 반환 금지 |

---

## 3. 파이프라인 순서

```
1. safety gate
   ↓ (pass)
2. extraction (LLM → date/time/dept/patient signals/proxy signals)
   - 이름, 연락처, 날짜 등 여러 정보를 한 문장에서 동시 추출 시도
   ↓
3. typo correction / normalization (신규)
   - "8ㅛㅣ" → "08:00" 등 명백한 오타 교정
   - 교정 제안이 필요한 경우, 사용자에게 되묻고 확인 (clarify의 특수 케이스)
   ↓
4. dialogue state merge
   - is_proxy_booking 확인 (미확인 시 최우선 clarify)
   - patient_name / patient_contact 수집 (미확보 시 다음 순위 clarify)
   ↓
5. storage lookup
   - 기존 예약 조회 (modify/cancel/check)
   - 초진/재진 판정 (patient_contact 우선)
   ↓
6. policy check (결정론)
   - 운영시간, 정원, 24시간, 슬롯 겹침
   ↓
7. (Q4) cal.com available slots 조회 및 booking 생성
   ↓
8. persist (bookings.json에 patient_contact 포함)
   ↓
9. response build
```

---

## 4. 핵심 상태머신: 환자 정보 수집 플로우

### 4.1 chat 모드 플로우

```
사용자 메시지
  → safety gate
  → 예약 의도 확정 (book/modify/cancel/check)
  → [본인/대리인 확인 단계]
      - 메시지에 대리 표현 감지? → is_proxy_booking=true, 환자 본인 정보 요청
      - 없으면? → "환자 본인이신가요, 대리 예약이신가요?" 질문
  → [환자 정보 수집]
      - is_proxy_booking=false: 본인 성명 + 전화번호
      - is_proxy_booking=true: 환자 본인 성명 + 전화번호
  → [초진/재진 판정]
      - storage.resolve_customer_type_from_history(patient_contact=...) 호출
  → [예약 정보 수집] (날짜/시간/분과 — 이미 있으면 스킵)
  → [정책 검사]
  → [확인 질문]
  → [영속화]
```

### 4.2 batch 모드 플로우

```
ticket 입력
  → safety gate
  → classification + extraction
  → 대리 표현 감지?
      - 있으면: is_proxy_booking=true → 환자 정보 확인 불가 → clarify 반환
      - 없으면: is_proxy_booking=false → ticket.customer_name을 patient_name 힌트로 사용
  → ticket에 전화번호 없음 → customer_type은 ticket.customer_type 힌트로만 사용
  → storage lookup (전화번호 없으면 이름 기반, 충돌 시 clarify)
  → policy check → response
```

---

## 5. 구현 Phase별 계획

### Phase 0. 환자 식별 기반 확보 (최우선 신규 작업)

**목표**: 전화번호 기반 환자 식별 및 본인/대리인 구분 기능을 코드에 반영

#### Phase 0a. storage.py 확장
- [ ] `find_bookings()`: `patient_contact` 파라미터 추가 (filters dict에 `patient_contact` 키 지원)
- [ ] `resolve_customer_type_from_history()`: `patient_contact` 파라미터 추가. 전화번호가 있으면 name 대신 전화번호로 먼저 조회
- [ ] `create_booking()` 관련: `patient_contact`, `is_proxy_booking` 필드가 레코드에 포함될 수 있도록 확인 (이미 `dict` pass-through이므로 스키마 제약 없음)

#### Phase 0b. dialogue state 확장 (agent.py 또는 dialogue state 관리 모듈)
- [ ] `is_proxy_booking: bool | None` 슬롯 추가 (None = 아직 묻지 않음)
- [ ] `patient_name: str | None` 슬롯 추가
- [ ] `patient_contact: str | None` 슬롯 추가
- [ ] `is_proxy_booking`이 None이고 예약 의도가 확정된 시점에 본인/대리인 clarify 질문 생성

#### Phase 0c. extraction 확장 (LLM prompt 또는 rule-based)
- [ ] **(개선)** LLM 프롬프트 수정: 이름, 연락처, 날짜, 시간 등 여러 정보를 한 문장에서 동시에 추출하여 단일 JSON 객체로 반환하도록 명시적으로 요구 (`F-049`).
- [ ] 전화번호 패턴(010-xxxx-xxxx, 01x-xxx-xxxx 등) 추출 로직 추가
- [ ] 대리 예약 감지 패턴("엄마", "아버지", "가족", "대신", "대리" 등) 추가
- [ ] 추출된 전화번호를 `patient_contact` 슬롯에 저장

---

### Phase 1. Dialogue State Machine 리팩터

**목표**: `clarify`를 4단계 누적 상태머신으로 안정화하고, 불필요한 escalation 방지

- [ ] `pending_missing_info_queue` 도입: 누락 정보를 우선순위 큐로 관리
  - 우선순위: ① is_proxy_booking 확인 → ② patient_contact → ③ patient_name → ④ dept/date/time → ⑤ birth_date (충돌 시)
- [ ] **(개선)** `clarify_turn_count` 상한 정책 완화 (`F-042`):
  - 오타 수정 제안과 같이 복구 가능한 `clarify`는 `clarify_turn_count`를 증가시키지 않거나, 가중치를 낮춰 성급한 escalation 방지.
- [ ] **(신규)** 오타 교정 제안 및 확인 로직 추가 (`F-040`):
  - 시간 등 특정 정보 추출 실패 시, 오타 가능성이 높은 경우(예: "8ㅛㅣ") 교정된 값을 제안하고 사용자에게 "예/아니오" 확인을 받는 미니 상태 도입.
- [ ] 이미 확보된 슬롯은 재질문하지 않도록 누적 슬롯 검사 강화
- [ ] 매 턴마다 전체 상태 재평가 (action 재분류 포함)
- [ ] 대체 슬롯 선택 상태 전환 (slots unavailable → candidate list → pending_slot_selection)

---

### Phase 2. Policy 확장

**목표**: 운영시간/점심시간/휴진일/슬롯 겹침 완전 반영

- [x] 평일(월-금) 09:00-18:00 검사
- [x] 토요일 09:00-13:00 검사
- [x] 일요일 무조건 불가
- [x] 점심시간 12:30-13:30 겹침 검사
- [x] 공휴일: 확정 불가 시 clarify/escalate
- [x] 초진 40분 슬롯 겹침 계산 (policy.py에 이미 있으나 운영시간과 연동 확인)
- [x] 슬롯 불가 시 대체안 제시 (같은 날 → 다음 가용일 순서)

---

### Phase 3. Safety / Extraction 강화

**목표**: 대리 예약, 보험/비용, 의사 개인정보, 타 환자 정보 처리 완전화

- [ ] 대리 예약 감지 패턴 강화 및 테스트 케이스 추가
- [ ] 전화번호 추출 정규식 추가
- [ ] 보험/비용 문의 escalate 처리
- [ ] 의사 개인정보/연락처 요청 escalate 처리
- [ ] 타 환자 정보 요청 reject 처리

---

### Phase 4. Persistence 강화

**목표**: 최종 확인 직전 fresh recheck + 저장 실패 처리

- [ ] confirmation 직전: `find_bookings()`로 정원/중복 fresh recheck
- [ ] modify/cancel 영속 반영 확인 (status 업데이트)
- [ ] 저장 실패 시 에러 캐치 → clarify/escalate 반환 (거짓 성공 금지)

---

### Phase 5. Metrics & Documents

**목표**: KPI 계측 및 문서 완성

- [ ] `agent_success`, `safe_reject`, `agent_soft_fail_clarify`, `agent_soft_fail_escalate`, `agent_hard_fail`, `clarify_resolved`, `clarify_abandoned` 이벤트 런타임 집계
- [ ] `docs/q1_metric_rubric.md` 작성
- [ ] `docs/q3_safety.md` 작성
- [ ] `docs/final_report.md` 최신화

---

## 6. storage.py 변경 사양 (Phase 0a 상세)

### 6.1 find_bookings() 변경

```python
# 현재
def find_bookings(
    customer_name: str | None = None,
    filters: dict[str, Any] | None = None,
    path: str | Path | None = None,
    include_cancelled: bool = False,
) -> list[dict]:

# 변경 후
def find_bookings(
    customer_name: str | None = None,
    filters: dict[str, Any] | None = None,
    path: str | Path | None = None,
    include_cancelled: bool = False,
    patient_contact: str | None = None,  # ★ 신규: 전화번호 우선 검색
) -> list[dict]:
```

내부 필터링에 다음 조건 추가:
```python
if patient_contact and booking.get("patient_contact") != patient_contact:
    continue
```

### 6.2 resolve_customer_type_from_history() 변경

```python
# 현재 시그니처
def resolve_customer_type_from_history(
    customer_name: str,
    birth_date: str | None = None,
    path: str | Path | None = None,
) -> dict[str, Any]:

# 변경 후
def resolve_customer_type_from_history(
    customer_name: str | None = None,
    birth_date: str | None = None,
    path: str | Path | None = None,
    patient_contact: str | None = None,  # ★ 신규: 전화번호 우선
) -> dict[str, Any]:
```

처리 로직:
1. `patient_contact`가 있으면 → `find_bookings(patient_contact=patient_contact, include_cancelled=True)`로 먼저 조회
2. 결과가 있으면 이름 기반 동명이인 문제를 건너뛰고 직접 초진/재진 판정
3. `patient_contact`가 없으면 → 기존 이름+생년월일 로직 유지

---

## 7. dialogue state 추가 필드 사양 (Phase 0b 상세)

```python
# 기존 상태 (예시)
class DialogueState:
    pending_action: str | None
    date: str | None
    time: str | None
    department: str | None
    customer_name: str | None
    clarify_turn_count: int

# 추가 필드
    is_proxy_booking: bool | None   # None = 아직 확인 안 함
    patient_name: str | None        # 실제 진료 받을 환자 이름
    patient_contact: str | None     # 환자 전화번호 (우선 식별자)
    birth_date: str | None          # 동명이인 해소용 보조 식별자
    pending_missing_info_queue: list[str]  # 누락 정보 우선순위 큐
```

### 7.1 missing_info_queue 우선순위

```
1. "is_proxy_booking"   → 본인/대리인 여부 확인
2. "patient_contact"    → 환자 전화번호
3. "patient_name"       → 환자 성명 (patient_contact와 함께 수집)
4. "department"         → 진료 분과
5. "date"               → 날짜
6. "time"               → 시간
7. "birth_date"         → 생년월일 (동명이인 충돌 시만)
8. "slot_selection"     → 대체 슬롯 선택 (슬롯 불가 시)
9. "confirmation"       → 최종 확인
```

---

## 8. 배치 모드 처리 원칙 (batch vs chat 차이)

| 항목 | chat 모드 | batch 모드 |
| --- | --- | --- |
| 본인/대리인 확인 | 명시적 질문 | 메시지에서 감지. 없으면 본인으로 간주 |
| 전화번호 수집 | clarify 멀티턴으로 수집 | 메시지에서 추출 시도. 없으면 ticket.customer_type 힌트 사용 |
| 초진/재진 판정 | 전화번호 기반 저장소 조회 우선 | ticket.customer_type 힌트 + 저장소 조회(이름 기반) |
| 대리 예약 처리 | 본인/대리인 질문 후 환자 정보 수집 | 대리 감지 시 clarify 반환 |
| 최종 확인 | 사용자 확인 후 영속화 | 정책 통과 시 즉시 반환 (영속화는 chat 흐름에서) |

---

## 9. 우선순위 및 비용 구조 기반 라우팅

하드 실패 1건 = 에스컬레이션 25건 = 성공 처리 50건 손실.

따라서:
1. **하드 실패 최우선 회피**: 의료 상담 오답, 허위 성공, 잘못된 개인정보 노출 → 절대 허용 안 함
2. **불필요한 escalation 축소**: 정보 부족/모호성은 clarify로 회수
3. **자동 성공률 향상**: 멀티턴 clarify 안정화로 recoverable case 자동 처리

---

## 10. 테스트 우선순위

| 테스트 파일 | 커버해야 할 케이스 |
| --- | --- |
| `tests/test_safety.py` | 의료 상담 reject, 인젝션 reject, 응급 escalate |
| `tests/test_storage.py` | patient_contact 필터, resolve_customer_type (전화번호 우선) |
| `tests/test_policy.py` | 24시간 경계값, 정원 3명 경계값, 초진 40분 슬롯 |
| `tests/test_dialogue.py` | 본인/대리인 확인 플로우, 4단계 clarify 누적, 누적 슬롯 유지 |
| `tests/test_batch.py` | confidence/reasoning 비하드코딩 검증 |
| `tests/test_classifier.py` | 7개 enum 외 값 반환 금지 |
