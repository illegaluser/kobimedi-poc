# 상세 구현 설계 계획서

기준 문서: `.ai/handoff/00_request.md`, `.ai/harness/features.json`, `docs/policy_digest.md`, `docs/architecture.md`, `AGENTS.md`, `.ai/harness/progress.md`  
목표: request.md와 features.json을 만족하는 **최종 구현 계획**을 세밀하게 정의한다. 이 문서는 이후 실제 코딩의 기준 문서다.  
우선순위: **safety > correctness > policy compliance > demo polish > Q4**

이 문서는 단순 일정표가 아니라 다음을 동시에 만족해야 한다.

1. 구현자가 바로 코딩에 착수할 수 있을 정도로 **구체적일 것**
2. `docs/policy_digest.md`, `docs/architecture.md`와 **동일한 사실만 말할 것**
3. 예외사항, 실패 처리, 테스트 전략, 데이터 계약까지 포함할 것
4. request.md의 Hard Constraints와 AGENTS.md의 Non-Negotiables를 **어느 항목보다 우선**할 것

---

## 0. 고정 전제와 절대 불변 조건

아래 항목은 구현 중 절대 바뀌면 안 된다.

### 0.1 파이프라인 순서
반드시 다음 순서를 유지한다.

1. **safety gate**
2. **classification**
3. **extraction**
4. **policy check**
5. **(Q4) cal.com**
6. **response build**

### 0.2 공통 로직 원칙
- `chat.py`와 `run.py`는 모두 **같은 `src/agent.py` 코어 로직**을 호출한다.
- chat/run이 서로 다른 정책 판단, 서로 다른 safety 분기, 서로 다른 JSON 스키마를 가지면 안 된다.

### 0.3 Action Enum 고정값
다음 7개를 **그대로** 사용한다.

- `book_appointment`
- `modify_appointment`
- `cancel_appointment`
- `check_appointment`
- `clarify`
- `escalate`
- `reject`

### 0.4 금지 사항
- 의료 판단 생성
- action enum 축약/변형
- chat/run 별도 로직
- 확인되지 않은 정책/가용시간/예약 존재 여부 생성
- confidence/reasoning 하드코딩

### 0.5 LLM/연동 고정 조건
- LLM: **Ollama `qwen3-coder:30b`**
- 구조화 출력: 반드시 `format='json'`
- cal.com 연동 로직: `src/calcom_client.py`
- 시간 의존 핵심 로직: `now` 파라미터 주입 가능해야 함

---

## 1. 목표 상태 요약

최종 구현은 아래 동작을 만족해야 한다.

1. unsafe 요청은 예약 흐름에 들어가기 전에 차단된다.
2. 안전한 요청은 7개 action 중 하나로 분류되고, 예약에 필요한 구조화 정보가 추출된다.
3. 숫자/시간/정원/24시간/기존 예약 판정은 `src/policy.py`가 결정론적으로 처리한다.
4. 예약 상태는 파일 기반 영속 저장소를 진실원천으로 사용한다.
5. chat는 멀티턴 clarify/확인 흐름을 지원하고, run은 같은 로직으로 배치 처리한다.
6. 결과 JSON은 과제 예시 키를 유지하며, confidence/reasoning은 실제 파이프라인 근거로 생성된다.
7. Q4 활성 시 policy 이후 cal.com slot 조회와 booking 생성이 붙는다.

---

## 2. 현재 코드베이스 대비 핵심 갭 분석

현재 진행 상태(`.ai/harness/progress.md`)를 고려할 때, 설계상 특히 보완이 필요한 부분은 다음과 같다.

### 2.1 이미 상당 부분 있는 영역
- safety-first 제어 흐름
- classifier / llm fallback 기본 구조
- policy 엔진 기본 구조
- chat 멀티턴 세션 골격
- batch JSON 기본 스키마
- confidence/reasoning 동적 산출 골격

### 2.2 여전히 설계/구현 보강이 필요한 영역
1. **F-004 혼합 요청 분리 처리의 명시적 데이터 계약**
2. **F-018 / F-036 / F-037 / F-038 / F-039 저장소 진실원천화**
3. **Q4(cal.com) 설계 및 실패 처리 세부화**
4. **문서(F-027, F-035)와 구현 계획의 완전한 정렬**
5. **테스트 축에 storage / cal.com / edge case mapping 보강**

즉, 이후 코딩은 “분류기 만들기”보다 **저장소·정책·대화상태·외부연동을 한 시스템으로 엮는 작업**이 핵심이다.

---

## 3. 기능 맵 (features.json 전체 반영)

아래는 features.json의 전체 기능을 구현 단위로 다시 정렬한 것이다.  
이 표는 이후 코딩/테스트/문서화의 기준이며, 빠뜨린 기능이 없어야 한다.

### 3.1 Safety
| ID | 목표 | 구현 핵심 |
| --- | --- | --- |
| F-001 | 의료 상담 요청 reject | safety rule + LLM fallback |
| F-002 | 목적 외 사용/인젝션 reject | keyword rule + LLM fallback |
| F-003 | 급성 통증/응급 escalate | safety stage short-circuit |
| F-004 | 의료+예약 혼합 요청 분리 | safe booking subrequest 계약 도입 |
| F-005 | 미확인 정보 비창작 | validator + response guard |

### 3.2 Classification / Extraction
| ID | 목표 | 구현 핵심 |
| --- | --- | --- |
| F-006 | 7개 action 정확 분류 | enum validator |
| F-007 | 분과 추정 | 명시 분과 / 의사 / 증상 기반 힌트 |
| F-008 | 증상 기반 분과 안내 | 의료 진단 금지 + guidance response |
| F-009 | 의사명→분과 매핑 | 고정 테이블 |
| F-010 | 날짜/시간/분과/대상 예약 정보 추출 | rule + LLM hybrid |
| F-011 | 정보 부족 시 clarify | action별 필수 정보 검사 |

### 3.3 Dialogue
| ID | 목표 | 구현 핵심 |
| --- | --- | --- |
| F-012 | clarify 이후 누적 컨텍스트 | session_state 누적 슬롯 |
| F-013 | 예약 2단계 확인 흐름 | pending_confirmation |
| F-014 | 여러 기존 예약 모호성 해소 | pending_candidates |
| F-031 | 가용 슬롯 제안 기반 2단계 흐름(Q4) | slot selection state |

### 3.4 Policy
| ID | 목표 | 구현 핵심 |
| --- | --- | --- |
| F-015 | 1시간당 최대 3명 | hourly capacity |
| F-016 | 변경/취소 24시간 규칙 | exact boundary handling |
| F-017 | 초진 40분 / 재진 30분 | duration-aware overlap |
| F-018 | modify/cancel/check 저장소 진실원천 검증 | storage-backed lookup |
| F-019 | 당일 신규 예약 보수 처리 | 일반=clarify, 응급=escalate |
| F-020 | 슬롯 불가 시 대체 시간 안내 | deterministic alternatives |

### 3.5 Runtime / Shared Core
| ID | 목표 | 구현 핵심 |
| --- | --- | --- |
| F-021 | chat.py가 shared core 사용 | `process_message`/`process_ticket` 공유 |
| F-022 | run.py가 shared core 사용 | batch adapter만 유지 |
| F-023 | 배치 JSON 키 정합성 | fixed output contract |
| F-024 | confidence/reasoning 비하드코딩 | rule-based synthesis |
| F-034 | chat/run 공통 cal.com 로직 공유 | `agent.py` orchestration |
| F-040 | now 주입 테스트 가능 | deterministic time tests |

### 3.6 Reliability / Evaluation
| ID | 목표 | 구현 핵심 |
| --- | --- | --- |
| F-025 | golden_eval 일반화 평가 | eval CLI + labels |
| F-026 | Ollama 실패 안전 폴백 | llm_client standard error payload |
| F-033 | cal.com 실패 안전 폴백 | external error normalization |
| F-039 | 저장소 실패 안전 폴백 | false success 금지 |
| F-041 | gold case는 원본 ticket 구조 유지 | evaluator input contract |

### 3.7 Storage
| ID | 목표 | 구현 핵심 |
| --- | --- | --- |
| F-036 | 신규 예약 저장소 반영 | create + persist |
| F-037 | 변경/취소 저장소 반영 | update/cancel persist |
| F-038 | chat/run 공통 저장소 계층 | shared storage module |

### 3.8 Integration / Documentation
| ID | 목표 | 구현 핵심 |
| --- | --- | --- |
| F-027 | final_report 필수 항목 포함 | 문서 체크리스트 |
| F-028 | cal.com 설정 로드 | env config layer |
| F-029 | 분과↔Event Type 매핑 | fixed mapping |
| F-030 | available slots 조회 | API wrapper |
| F-032 | 실제 booking 생성 | booking API wrapper |
| F-035 | Q4 외부 준비사항 문서화 | setup checklist |

---

## 4. 구현 Phase 순서 (실제 코딩 순서)

코딩은 아래 순서를 따른다. 이 순서를 깨면 회귀 위험이 커진다.

### Phase 0. 문서 동기화 완료
- `policy_digest.md`, `architecture.md`, `10_plan.md` 일치화
- 용어 통일: action, safety category, storage truth source, policy order

### Phase 1. 저장소 계층 도입/정리
- `src/storage.py` 신설 권장
- load / save / list / find / create / modify / cancel API 정의
- `agent.py` 내부 파일 읽기 helper를 storage 계층으로 이동

### Phase 2. Safety 완결
- F-001~F-005
- mixed request의 safe subrequest 계약 확정

### Phase 3. Classification / Extraction 완결
- F-006~F-011
- 모든 누락 정보가 action별로 명시적으로 계산되도록 정리

### Phase 4. Policy 완결
- F-015~F-020, F-040
- policy 입출력 스키마 고정

### Phase 5. Dialogue 완결
- F-012~F-014
- 세션 상태 누적/확인/후보선택 분기 정리

### Phase 6. Persistence 연결
- F-018, F-036, F-037, F-038, F-039
- 예약 성공/변경/취소가 실제 저장으로 이어지도록 연결

### Phase 7. Runtime / Evaluation 정리
- F-021~F-026, F-041
- batch 계약, golden eval, confidence/reasoning 점검

### Phase 8. Q4 cal.com 구현
- F-028~F-035
- slot lookup / booking / sync / graceful failure

### Phase 9. 제출 문서 마감
- F-027, F-035
- final_report 및 데모 증빙 정리

---

## 5. 파일 구조 계획

최종 목표 기준 권장 파일 구조는 다음과 같다.

```text
chat.py
run.py

src/
  agent.py
  classifier.py
  llm_client.py
  policy.py
  response_builder.py
  calcom_client.py
  storage.py          # 신규 권장
  models.py           # 필요 시 결과/상태 dataclass 또는 typed dict
  utils.py
  prompts.py
```

### 5.1 파일별 책임

#### `chat.py`
- CLI 입출력만 담당
- 세션 생성 / 세션 유지
- 비즈니스 로직 없음

#### `run.py`
- 입력 JSON 로드
- 각 ticket에 대한 `process_ticket()` 호출
- 결과 JSON 저장
- 비즈니스 로직 없음

#### `src/agent.py`
- 전체 오케스트레이션
- safety → classify/extract → session merge → storage lookup → policy → persist/cal.com → response
- runtime fields(`ticket_id`, `classified_intent`, `confidence`, `reasoning`) 최종 보장

#### `src/classifier.py`
- safety 결과 생성
- intent/extraction 생성
- doctor/department/date/time normalization
- enum 및 분과 validator

#### `src/llm_client.py`
- Ollama 호출 공통 계층
- JSON parse / retry / 표준 에러 페이로드

#### `src/policy.py`
- 결정론 정책 판정 엔진
- 입력이 동일하면 항상 동일 결과를 반환해야 함

#### `src/storage.py` (신규 권장)
- 로컬 JSON 파일 CRUD 공통 계층
- atomic write / 파일 손상 방어 / 상태 업데이트 처리

#### `src/calcom_client.py`
- env config load
- event type mapping
- available slots 호출
- booking 생성
- 외부 오류 정규화

#### `src/response_builder.py`
- 사용자 자연어 응답 문장 조립
- 예약 요약 문구 생성
- clarify 질문 / 옵션 질문 / 성공 문구 생성

---

## 6. 데이터 계약 상세

이 절은 구현 시 함수 간 입출력 형식을 통일하기 위한 것이다.

### 6.1 Ticket 입력 계약

```python
ticket = {
    "ticket_id": "T-001",
    "customer_name": "김민수",
    "customer_type": "재진",
    "message": "내일 오후 2시에 이비인후과 예약하고 싶습니다",
    "timestamp": "2025-03-16T09:30:00+09:00",
    "context": {
        "has_existing_appointment": False,
        "preferred_department": "이비인후과"
    }
}
```

### 6.2 `safety_result` 계약

```python
safety_result = {
    "category": "safe" | "medical_advice" | "off_topic" | "emergency" | "complaint" | "classification_error",
    "reason": str,
    "is_medical": bool,
    "is_off_topic": bool,
    "is_emergency": bool,
    "mixed_department_guidance": bool,
    "department_hint": str | None,
    "unsupported_department": str | None,
    "unsupported_doctor": str | None,
    "contains_booking_subrequest": bool,
    "safe_booking_text": str | None,
    "fallback_action": str | None,
    "fallback_message": str | None,
}
```

### 6.3 `intent_result` 계약

```python
intent_result = {
    "action": str,
    "department": str | None,
    "doctor_name": str | None,
    "date": "YYYY-MM-DD" | None,
    "time": "HH:MM" | None,
    "booking_time": str | None,
    "customer_type": "초진" | "재진" | None,
    "is_first_visit": bool | None,
    "missing_info": list[str],
    "target_appointment_hint": dict | None,
    "error": bool | None,
    "fallback_action": str | None,
    "fallback_message": str | None,
}
```

### 6.4 `policy_result` 계약

```python
policy_result = {
    "allowed": bool,
    "reason_code": str,
    "reason": str,
    "recommended_action": str | None,
    "needs_alternative": bool,
    "alternative_slots": list[str],
    "slot_duration_minutes": int | None,
    "same_day": bool | None,
}
```

### 6.5 `session_state` 계약

```python
session_state = {
    "conversation_history": list[dict],
    "accumulated_slots": {
        "date": str | None,
        "time": str | None,
        "department": str | None,
    },
    "pending_confirmation": {
        "action": str,
        "appointment": dict,
        "slot_options": list[dict] | None,
    } | None,
    "pending_action": str | None,
    "pending_missing_info": list[str],
    "pending_candidates": list[dict] | None,
}
```

### 6.6 `storage_record` 계약

최소 필드:

```python
appointment = {
    "id": str,
    "customer_name": str,
    "booking_time": str,
    "department": str,
    "customer_type": "초진" | "재진",
}
```

권장 확장 필드:

```python
appointment = {
    "id": str,
    "customer_name": str,
    "booking_time": str,
    "department": str,
    "customer_type": "초진" | "재진",
    "status": "active" | "cancelled",
    "created_at": str,
    "updated_at": str,
    "cancelled_at": str | None,
    "source": "local" | "calcom",
    "external_booking_id": str | None,
}
```

### 6.7 최종 결과 계약

```python
result = {
    "ticket_id": str | None,
    "classified_intent": str,
    "department": str | None,
    "action": str,
    "response": str,
    "confidence": float,
    "reasoning": str,
}
```

---

## 7. 세부 구현 계획: Safety

### F-001 의료 상담 요청 reject
**구현 위치**: `src/classifier.py`, `src/agent.py`

#### 요구 동작
- 진단, 약물, 처방, 치료 방법, 의학적 판단 요청은 무조건 `reject`
- 정책/예약 단계로 넘어가면 안 됨

#### 구현 방법
- keyword rule 우선
- 애매한 케이스는 `_call_safety_llm()`로 보조 판별
- `agent.py`에서 safety category가 `medical_advice`이면 즉시 응답 반환

#### 예외
- 증상 기반 분과 문의는 의료 상담이 아니라 예약 안내로 분리
- “진료 예약”처럼 `진료`라는 단어가 들어가도 예약 맥락이면 차단 금지

### F-002 목적 외 사용 / 인젝션 reject
**구현 위치**: `src/classifier.py`, `src/agent.py`

#### 요구 동작
- 날씨, 잡담, 타 서비스 문의, 내부 프롬프트 공개 요구 차단

#### 구현 방법
- `INJECTION_PATTERNS`, `OFF_TOPIC_PATTERNS` 유지/보강
- 인젝션과 예약 요청이 혼합된 경우에도 내부 지침은 절대 노출 금지

### F-003 응급 escalate
**구현 위치**: `src/classifier.py`, `src/agent.py`, `src/response_builder.py`

#### 요구 동작
- 급성 통증 / 응급 / 즉시 진료 강요는 자동 예약이 아니라 `escalate`

#### 구현 방법
- regex rule 우선, subtle 표현은 LLM 보조
- 응답 문구는 “상담원 또는 의료진 확인이 먼저 필요”로 통일

### F-004 의료+예약 혼합 요청 분리
**구현 위치**: `src/classifier.py`, `src/agent.py`

#### 목표 상태
혼합 요청은 다음 세 가지 중 하나여야 한다.

1. 의료 부분 거부 + 예약 부분 후속 처리
2. 의료 부분 거부 + 예약 부분 clarify
3. 안전 분리 불가 시 전체 reject

#### 추가 구현 항목
- `safety_result.contains_booking_subrequest`
- `safety_result.safe_booking_text`
- 필요 시 mixed-intent 전용 parsing helper

#### 예외 규칙
- “이 약 먹어도 되나요?”만 있으면 전체 `reject`
- “이 약 먹어도 되나요? 그리고 내일 예약”은 예약 텍스트만 downstream으로 전달 가능
- 의료 조언 응답과 예약 응답이 한 문장에서 섞이더라도 의료 판단은 생성 금지

### F-005 미확인 정보 비창작
**구현 위치**: `src/classifier.py`, `src/agent.py`, `src/response_builder.py`

#### 체크 항목
- 지원하지 않는 분과
- 지원하지 않는 의사
- 확인되지 않은 가용시간
- 저장소에 없는 기존 예약

#### 구현 방법
- validator 함수로 normalize 실패 시 `None`
- 최종 문장 조립 시 `None` 기반으로만 응답 생성
- 없는 값을 임의 대체하지 않음

---

## 8. 세부 구현 계획: Classification / Extraction

### F-006 7개 action 정확 분류
**구현 위치**: `src/classifier.py`, `src/llm_client.py`, `src/prompts.py`

#### 구현 원칙
- rule-based prior + LLM 보조
- 최종 action은 validator로 7개 enum만 허용
- enum 밖 값은 `clarify`

### F-007 분과 추정
**구현 위치**: `src/classifier.py`

#### 추정 우선순위
1. 명시 분과
2. 의사명 매핑
3. 증상 기반 분과 힌트
4. ticket.context.preferred_department
5. 그래도 불명확하면 `None`

### F-008 증상 기반 분과 안내
**구현 위치**: `src/classifier.py`, `src/response_builder.py`

#### 응답 원칙
- “예약 안내 기준으로는 이비인후과가 적절할 수 있습니다” 수준까지 허용
- “감기 같습니다”, “약 드세요” 같은 문구 금지

### F-009 의사명→분과 매핑
**구현 위치**: `src/classifier.py`, `src/policy.py` 또는 `src/utils.py`

#### 고정 매핑
- 이춘영 원장 → 이비인후과
- 김만수 원장 → 내과
- 원징수 원장 → 정형외과

#### 예외
- 지원하지 않는 의사명은 추정 금지

### F-010 핵심 슬롯 정보 추출
**구현 위치**: `src/classifier.py`

#### 추출 대상
- 날짜
- 시간
- 분과
- 의사명
- customer_type / first_visit
- 기존 예약 대상 힌트

#### 구현 원칙
- rule extraction 우선
- LLM extraction 보조
- 상대 날짜 표현은 반드시 `now` 기준 해석

#### 예외
- 날짜만 있고 시간이 없으면 missing info 유지
- “다음 주 화요일”은 local timezone 기준으로 정확히 계산
- 12시/정오/자정/반 표현 지원

### F-011 정보 부족 시 clarify
**구현 위치**: `src/agent.py`, `src/response_builder.py`

#### action별 clarify 조건
- `book_appointment`: 분과/날짜/시간/고객유형 부족
- `modify_appointment`: 기존 예약 대상 불명확, 새 시간 누락
- `cancel_appointment`: 기존 예약 대상 불명확
- `check_appointment`: 대상 예약 불명확

#### 구현 규칙
- 누락 필드는 리스트로 계산
- 질문은 한 번에 너무 길지 않게, 우선순위 높은 항목부터 묻기

---

## 9. 세부 구현 계획: Dialogue / Session

### F-012 clarify 후 정보 누적
**구현 위치**: `src/agent.py`

#### 누적해야 할 것
- date
- time
- department
- pending action
- pending missing info

#### 구현 포인트
- 새 입력이 일부 슬롯만 포함하더라도 이전 슬롯과 merge
- 누적 정보가 완성되면 다시 book/modify/cancel/check로 승격

### F-013 예약 2단계 확인 흐름
**구현 위치**: `src/agent.py`, `src/response_builder.py`

#### chat 모드 목표 흐름
1. 예약 요청 분석
2. 정책 통과
3. “~로 예약할까요?” 질문
4. “네” → 실제 확정
5. “아니요” → clarify로 복귀

#### batch 모드 차이
- 세션이 없으므로 confirm 대기 없이 단일 결과만 반환
- 문서/테스트에서 이 차이를 명확히 유지

### F-014 여러 기존 예약 모호성 해소
**구현 위치**: `src/agent.py`, `src/response_builder.py`

#### 목표 흐름
1. 고객 이름 기반 기존 예약 목록 조회
2. 분과/날짜/시간 힌트로 후보 좁힘
3. 여전히 여러 건이면 번호 리스트 질문
4. 사용자 답변으로 한 건 선택
5. 이후 정책 적용

#### 예외
- 번호, 분과명, 날짜, 시간 어느 방식으로 답해도 선택 가능하게 처리

---

## 10. 세부 구현 계획: Policy

### F-015 1시간당 최대 3명
**구현 위치**: `src/policy.py`

#### 규칙
- 요청 시작 시각이 속한 local hour window 기준
- 3명까지 허용, 4번째부터 불가

#### 주의점
- 단순 “같은 시작시각”이 아니라 “같은 1시간 창” 기준
- 기존 예약이 14:00, 14:20, 14:40이면 14시 창은 full

### F-016 24시간 변경/취소 규칙
**구현 위치**: `src/policy.py`

#### 규칙
- `now <= appointment_time - 24h` 이면 허용
- 정확히 24시간 전은 허용
- 24시간보다 늦으면 불가

#### 주의점
- naive datetime 금지, timezone-aware로 normalize

### F-017 초진 40분 / 재진 30분
**구현 위치**: `src/policy.py`

#### 규칙
- 초진: 40분
- 재진: 30분
- overlap 계산은 duration-aware

#### 예외
- customer_type 누락 시 availability 계산 금지 → `clarify`

### F-018 기존 예약 진실원천 검증
**구현 위치**: `src/storage.py`, `src/agent.py`, `src/policy.py`

#### 규칙
- modify/cancel/check는 `ticket.context`만으로 처리하지 않는다.
- 저장소에 실제 예약이 있어야 한다.

#### 구현 항목
- `find_customer_appointments(customer_name, status='active')`
- `find_matching_appointments(customer_name, filters)`

### F-019 일반 당일 신규 예약 보수 처리
**구현 위치**: `src/policy.py`

#### 규칙
- 일반 당일 신규 예약: 자동 확정 금지
- 응급/급성 통증은 safety에서 `escalate`
- 일반 당일 예약은 `clarify` + 다음날 또는 대체시간 안내

### F-020 슬롯 불가 시 대체안 안내
**구현 위치**: `src/policy.py`, `src/response_builder.py`

#### 구현 원칙
- 요청 슬롯 이후 인접 슬롯 탐색
- 대체안 1~3개 정도 제시
- 단순 “안 됩니다”로 끝내지 않음

---

## 11. 세부 구현 계획: Storage

이 영역은 이후 코딩에서 가장 중요하다. features.json 업데이트의 핵심 반영 대상이다.

### F-036 신규 예약 영속화
**구현 위치**: `src/storage.py`, `src/agent.py`

#### 목표 동작
- 예약 확정 후 `data/appointments.json`에 실제 레코드 추가
- 다음 요청에서 즉시 조회 가능

#### 권장 함수
```python
def load_appointments(path: Path | None = None) -> list[dict]: ...
def save_appointments(appointments: list[dict], path: Path | None = None) -> None: ...
def create_appointment(record: dict, path: Path | None = None) -> dict: ...
```

#### 구현 세부
- id 생성 규칙 필요 (`appt-###` 또는 UUID)
- `created_at`, `updated_at`, `status='active'` 권장
- atomic write 권장: temp file 작성 후 replace

### F-037 변경/취소 영속화
**구현 위치**: `src/storage.py`, `src/agent.py`

#### 목표 동작
- modify: 기존 예약 레코드 갱신
- cancel: soft delete(`status='cancelled'`) 권장
- 이후 조회는 최신 상태 기준

#### 권장 함수
```python
def update_appointment(appointment_id: str, changes: dict, path: Path | None = None) -> dict: ...
def cancel_appointment(appointment_id: str, path: Path | None = None) -> dict: ...
```

### F-038 chat/run 공통 저장소 계층
**구현 위치**: `src/storage.py`, `chat.py`, `run.py`, `src/agent.py`

#### 원칙
- chat/run이 파일을 직접 만지지 않음
- storage 계층 하나로 read/write 수행

### F-039 저장 실패 안전 복구
**구현 위치**: `src/storage.py`, `src/agent.py`

#### 실패 케이스
- 파일 없음
- JSON 깨짐
- write permission 오류
- 동시 쓰기 중 충돌

#### 복구 원칙
- 예약 성공 메시지 금지
- `reject` 또는 `clarify` 또는 사람 확인 유도
- 오류는 reasoning 또는 로그에 반영 가능하되 사용자에게 내부 구현 세부를 과도하게 노출하지 않음

---

## 12. 세부 구현 계획: Runtime / Output

### F-021 chat.py shared core
**구현 위치**: `chat.py`, `src/agent.py`

#### 역할 분리
- chat.py: 입출력 루프, exit 처리, session 생성
- agent.py: 모든 판단

### F-022 run.py shared core
**구현 위치**: `run.py`, `src/agent.py`

#### 역할 분리
- run.py: JSON 입력/출력과 `now` 결정만 담당

### F-023 배치 JSON 계약
필수 키:

- `ticket_id`
- `classified_intent`
- `department`
- `action`
- `response`
- `confidence`
- `reasoning`

누락 금지, 의미 드리프트 금지.

### F-024 confidence / reasoning 동적 산출
**구현 위치**: `src/agent.py`

#### confidence 산식 설계 원칙
- safety 단계에서 명확히 판정되면 높게
- 필수 정보 부족이면 낮춤
- policy 통과 시 가중치 증가
- fallback/오류가 있었으면 감점

#### reasoning 구성 원칙
- safety 근거
- 분류 근거
- 추출된 슬롯 정보
- 정책 판정 근거
- clarify/escalate/reject 사유

하드코딩 문구 한 줄로 때우지 말고 실제 판단 근거를 조합한다.

### F-040 now 주입 테스트 가능성
**구현 위치**: `src/agent.py`, `src/classifier.py`, `src/policy.py`, `run.py`

#### 원칙
- 상대 날짜 파싱, 24시간 계산, same-day 판정, success message 날짜 표현은 모두 `now` 기준이어야 함

---

## 13. 세부 구현 계획: Reliability / Evaluation

### F-025 golden_eval
**구현 위치**: `golden_eval/eval.py`

#### 목표
- gold case와 result 비교
- action accuracy, department accuracy, safety miss 등을 별도 계산

### F-026 Ollama 실패 안전 폴백
**구현 위치**: `src/llm_client.py`, `src/classifier.py`, `src/agent.py`

#### 실패 유형
- connection refused
- timeout
- invalid response
- invalid JSON

#### 복구 규칙
- 위험한 허용 금지
- 일반적으로 `clarify`
- 안전성 판정 완전 실패 + 애매한 문장인 경우 `reject` 또는 `clarify`

### F-041 gold case 계약 유지
**구현 위치**: `golden_eval/gold_cases.json`, `golden_eval/eval.py`

#### 원칙
- 원본 tickets 구조 유지
- expected label만 추가
- note에 사람 검증 필요 명시

---

## 14. 세부 구현 계획: Q4 cal.com

이 절은 선택 과제지만 설계는 미리 확정한다.

### F-028 cal.com 설정 로드
**구현 위치**: `src/calcom_client.py`

#### 권장 환경변수
- `CALCOM_API_KEY`
- `CALCOM_BASE_URL` (없으면 기본값 사용 가능)
- event type mapping 관련 식별자(필요 시 slug 기반 처리)

#### 권장 함수
```python
def load_calcom_config() -> dict: ...
```

### F-029 분과 ↔ Event Type 매핑
고정 매핑:

- 이비인후과 → `ent-consultation`
- 내과 → `internal-medicine`
- 정형외과 → `orthopedics`

### F-030 available slots 조회
**구현 위치**: `src/calcom_client.py`, `src/agent.py`

#### 호출 조건
- action=`book_appointment`
- 필수 정보 확보
- policy 통과
- config enabled

#### 출력 정규화 예시
```python
{
    "enabled": True,
    "status": "slots_proposed",
    "event_type": "internal-medicine",
    "slot_options": [
        {"start": "2026-03-25T14:00:00+09:00", "end": "2026-03-25T14:30:00+09:00"}
    ]
}
```

### F-031 가용 슬롯 2단계 흐름
**구현 위치**: `src/agent.py`, `src/response_builder.py`

#### 목표 흐름
1. requested slot 또는 requested date를 바탕으로 slot 조회
2. 사용자에게 후보 제시
3. 사용자가 하나 선택
4. booking 생성

### F-032 실제 booking 생성
**구현 위치**: `src/calcom_client.py`, `src/agent.py`

#### 권장 함수
```python
def create_booking(slot_payload: dict, ticket: dict, config: dict) -> dict: ...
```

#### 후속 처리
- cal.com booking 성공 → 로컬 저장소 동기화
- 외부 booking id를 `external_booking_id`로 저장 권장

### F-033 cal.com 실패 폴백
**구현 위치**: `src/calcom_client.py`, `src/agent.py`

#### 실패 유형
- config missing
- available slots API 실패
- booking API 실패
- malformed response

#### 복구 원칙
- 거짓 성공 금지
- slot 미확인인데 “가능합니다” 금지
- booking 미생성인데 “완료되었습니다” 금지

### F-034 chat/run 공통 Q4 로직
**구현 위치**: `src/agent.py`, `chat.py`, `run.py`

#### 원칙
- cal.com 직접 호출은 adapter에서 하지 않음
- agent core만 외부 연동을 orchestration

### F-035 문서화
문서에 반드시 포함:
- cal.com 가입
- API key 준비
- 3개 Event Type 생성
- available slots / booking 테스트 방법
- 캘린더 스크린샷 확보 절차

---

## 15. Action별 상세 플로우

### 15.1 book_appointment
1. empty message 아니면 safety 실행
2. unsafe면 reject/escalate 종료
3. intent/extraction 수행
4. session_state와 merge
5. 분과/날짜/시간/고객유형 부족 시 clarify
6. same-day 일반 예약이면 clarify + 대체안
7. capacity/overlap 통과 시
   - chat: confirmation 질문
   - batch: 단일 턴 success 또는 제안 결과
8. 확정 시 storage create
9. Q4 enabled면 cal.com booking 또는 slot proposal
10. response + runtime fields 생성

### 15.2 modify_appointment
1. 기존 예약 후보 찾기
2. 없으면 clarify
3. 여러 건이면 clarify
4. 24시간 rule 통과 여부 확인
5. 새 시간 미제공이면 clarify
6. 새 시간 slot availability 검사
7. storage update
8. success response

### 15.3 cancel_appointment
1. 기존 예약 후보 찾기
2. 후보 모호성 해소
3. 24시간 rule 검사
4. storage cancel
5. success response

### 15.4 check_appointment
1. 기존 예약 후보 찾기
2. 여러 건이면 clarify
3. 한 건이면 summary response

### 15.5 clarify
발생 조건:
- 정보 부족
- 후보 모호성
- same-day 일반 예약
- external/LLM/storage 폴백

### 15.6 escalate
발생 조건:
- 응급
- 급성 통증
- 반복 민원
- 사람이 최종 결정해야 하는 예외

### 15.7 reject
발생 조건:
- 의료 상담
- 목적 외 사용
- 프롬프트 인젝션
- 미확인 분과/의사에 대해 답변 생성이 필요한 상황

---

## 16. 예외 및 경계값 처리 매뉴얼

구현 중 반드시 빠짐없이 처리해야 하는 케이스다.

### 16.1 시간 경계값
- 정확히 24시간 전 변경/취소 → 허용
- 24시간에서 1초라도 늦음 → 불가
- 오늘/내일/모레/글피 → local timezone 기준
- 다음 주 화요일 → 현재 주가 아닌 다음 주 화요일

### 16.2 정원/겹침
- 14:00/14:20/14:40으로 3명이 있으면 14시 창 full
- 초진 40분은 14:00~14:40 점유
- 재진 30분은 14:00~14:30 점유

### 16.3 고객유형
- 초진/재진 누락 → clarify
- “처음 방문” → 초진으로 normalize
- “재진” 명시 → 재진

### 16.4 기존 예약
- `context.has_existing_appointment=true`라도 저장소 없으면 있다고 답하면 안 됨
- 동일 고객 다수 예약 → clarify
- 번호/분과/날짜/시간으로 후보 선택 가능해야 함

### 16.5 안전 경계
- 증상 + 예약 문의는 safe 가능
- 증상 + 진단 요구는 reject
- 불만 + 예약 변경 문의는 escalate 우선
- 인젝션 + 예약 요청 혼합 시 내부 지침 노출 금지

### 16.6 외부 실패
- Ollama down
- storage write fail
- cal.com API fail

셋 모두 공통적으로 “완료”라고 말하지 않는 것이 핵심이다.

---

## 17. 테스트 계획 (기능 매핑 포함)

### 17.1 `tests/test_safety.py`
검증 기능:
- F-001
- F-002
- F-003
- F-004
- F-005

필수 케이스:
- 의료 상담
- off-topic
- prompt injection
- 응급
- complaint escalation
- mixed department guidance
- unknown department
- unknown doctor
- safety LLM fallback / connection refused / timeout

### 17.2 `tests/test_classifier.py`
검증 기능:
- F-006
- F-007
- F-008
- F-009
- F-010
- F-011
- F-026
- F-040

필수 케이스:
- enum validation
- doctor mapping
- symptom-based department inference
- relative date parsing
- time parsing(반/정오/자정)
- missing_info 계산

### 17.3 `tests/test_policy.py`
검증 기능:
- F-015
- F-016
- F-017
- F-018
- F-019
- F-020
- F-040

필수 케이스:
- 3명/4명 경계
- exact 24h boundary
- less than 24h block
- first visit overlap
- revisit overlap
- same-day general booking
- same-day emergency booking
- ambiguous existing appointment
- alternative slot generation

### 17.4 `tests/test_dialogue.py`
검증 기능:
- F-012
- F-013
- F-014
- F-021

필수 케이스:
- clarify slot accumulation
- two-step confirmation
- ambiguous appointment resolution
- batch without session remains single-turn

### 17.5 `tests/test_batch.py`
검증 기능:
- F-022
- F-023
- F-024
- F-034

필수 케이스:
- batch output keys complete
- valid action enum only
- confidence/reasoning populated
- shared core invocation

### 17.6 `tests/test_storage.py` (신규 권장)
검증 기능:
- F-018
- F-036
- F-037
- F-038
- F-039

필수 케이스:
- create persists
- modify persists
- cancel persists
- load corrupted json fail-safe
- write failure no false success
- chat/run shared path usage

### 17.7 `tests/test_calcom.py` (신규 권장)
검증 기능:
- F-028
- F-029
- F-030
- F-031
- F-032
- F-033
- F-034

필수 케이스:
- config missing graceful skip
- event type mapping
- slot lookup normalization
- booking create normalization
- API failure fallback

### 17.8 `tests/test_generalization.py`
검증 기능:
- F-004
- F-005
- F-012
- F-013
- F-014
- F-025
- F-033
- F-041

필수 케이스:
- 한국어 injection 변형
- 의료+예약 혼합
- 존재하지 않는 분과/의사
- 불명확 환자 유형
- 복수 예약 후보
- external failure edge case

---

## 18. 구현 체크리스트 (코딩 착수용)

### 18.1 storage 선행 작업
- [ ] `src/storage.py` 신설
- [ ] load/save/create/update/cancel/find API 정의
- [ ] atomic write 도입
- [ ] `agent.py`의 직접 파일 로드 helper 대체

### 18.2 safety/classifier 정리
- [ ] mixed request subrequest 계약 추가
- [ ] intent_result 필드 정규화
- [ ] missing_info 계산 일원화

### 18.3 policy 정리
- [ ] policy output schema 고정
- [ ] same-day / 24h / capacity / overlap 경계값 테스트 보강

### 18.4 runtime 연결
- [ ] book success 시 storage create
- [ ] modify success 시 storage update
- [ ] cancel success 시 storage cancel
- [ ] check 시 storage lookup only

### 18.5 Q4
- [ ] calcom_client 구현 또는 보강
- [ ] config loader
- [ ] slots 조회
- [ ] booking 생성
- [ ] storage sync

### 18.6 문서/평가
- [ ] final_report 체크리스트 정리
- [ ] demo evidence 정리
- [ ] golden eval 최신화

---

## 19. 문서 일관성 규칙

이 문서와 다른 문서가 절대 어긋나면 안 되는 핵심 문장들이다.

1. safety gate가 항상 첫 단계다.
2. action은 7개 고정값이다.
3. 일반 당일 신규 예약은 자동 확정하지 않는다.
4. modify/cancel/check는 저장소가 진실원천이다.
5. chat.py와 run.py는 같은 `src/agent.py`를 사용한다.
6. cal.com은 policy 이후에만 호출한다.
7. 미확인 정보는 생성하지 않는다.
8. confidence/reasoning은 하드코딩하지 않는다.

문서 업데이트나 코드 변경 시 위 8개가 깨지면 반드시 다시 정렬해야 한다.

---

## 20. 최종 구현 완료 정의 (Definition of Done)

아래를 모두 만족해야 “구현 완료”로 본다.

### 기능 완료
- F-001 ~ F-041 전체가 코드/문서/테스트 관점에서 설명 가능

### 실행 완료
- `python chat.py` 동작
- `python run.py --input tickets.json --output results.json` 동작

### 정책 완료
- safety-first
- 24시간 규칙
- 1시간 3명
- 초진 40분 / 재진 30분
- same-day 일반 예약 보수 처리

### 저장소 완료
- 신규 예약이 저장됨
- 변경/취소가 저장됨
- 조회/변경/취소가 저장소 기준으로 동작함

### 품질 완료
- 테스트 통과
- golden eval 실행 가능
- 문서 간 서술 불일치 없음

### Q4 완료(선택)
- slot 조회 가능
- booking 생성 가능
- 캘린더 증빙 가능

---

## 21. 마무리 메모

이후 코딩은 이 문서를 기준으로 진행한다.  
특히 우선순위는 항상 다음과 같다.

1. **unsafe를 먼저 막는다**
2. **정책을 코드로 고정한다**
3. **저장소를 진실원천으로 만든다**
4. **chat/run을 같은 코어로 묶는다**
5. **Q4는 그 위에 얹는다**

즉, 구현의 핵심은 “대답 잘하는 챗봇”이 아니라 **안전하고 일관되며 실제 예약 상태를 반영하는 예약 오케스트레이터**를 만드는 것이다.