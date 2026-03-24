# Q2 Agent Architecture

기준 문서: `.ai/handoff/00_request.md`, `AGENTS.md`  
설계 우선순위: **safety > correctness > policy compliance > demo polish > Q4**

이 문서는 코비메디 예약 Agent PoC의 Q2 아키텍처 골격을 정리한다. 핵심 목표는 다음 두 가지다.

1. `chat.py`와 `run.py`가 **동일한 `src/agent.py` 로직**을 공유할 것
2. 예약 자동화보다 먼저 **안전성 게이트와 결정론 정책**을 우선 적용할 것

---

## 1. 전체 아키텍처 개요

### 1.1 End-to-End 텍스트 다이어그램

```text
[입력]
  ├─ chat.py: 인터랙티브 사용자 메시지
  └─ run.py: 배치 ticket JSON
        ↓
[공통 엔트리포인트: src/agent.py]
        ↓
[안전성 게이트]
  ├─ 의료 상담 요청 차단 -> reject
  ├─ 목적 외 사용 차단 -> reject
  ├─ 프롬프트 인젝션 차단 -> reject
  └─ 급성 통증 / 응급 상황 감지 -> escalate
        ↓
[의도 분류]
  ├─ book_appointment
  ├─ modify_appointment
  ├─ cancel_appointment
  ├─ check_appointment
  ├─ clarify
  ├─ escalate
  └─ reject
        ↓
[슬롯 추출]
  ├─ 날짜 / 시간
  ├─ 분과 / 의사
  ├─ customer_type(초진/재진)
  └─ 기존 예약 대상 식별 정보
        ↓
[정책 검사]
  ├─ 24시간 변경/취소 규칙
  ├─ 1시간당 최대 3명 제한
  ├─ 초진 40분 / 재진 30분
  ├─ 당일 신규 예약 보수 처리
  └─ 대상 예약 존재 여부 검증
        ↓
[(Q4) cal.com 연동]
  ├─ Event Type 매핑
  ├─ available slots 조회
  └─ booking 생성
        ↓
[응답 생성]
  ├─ 정책 결과 기반 문장 생성
  ├─ 과제 JSON 키 정합성 보장
  └─ confidence / reasoning 구성
        ↓
[출력]
  ├─ chat.py: 대화 응답
  └─ run.py: results.json
```

### 1.2 단계별 책임 요약

| 단계 | 책임 | 핵심 출력 |
| --- | --- | --- |
| 입력 | 사용자 메시지/티켓을 공통 포맷으로 정규화 | normalized request |
| 안전성 게이트 | 의료 상담, 목적 외 사용, 인젝션, 응급 신호 우선 차단 | `reject` 또는 `escalate` 후보 |
| 의도 분류 | 과제 원문 7개 action 중 하나로 분류 | `classified_intent` |
| 슬롯 추출 | 예약 처리에 필요한 구조화 필드 추출 | 날짜/시간/분과/고객유형/대상 예약 |
| 정책 검사 | 병원 정책을 결정론적으로 적용 | 허용/불가/추가확인 결과 |
| cal.com | 실제 가용 슬롯 조회 및 예약 생성(Q4) | slot options / booking result |
| 응답 생성 | 사용자 문장과 배치 JSON을 일관되게 구성 | `response`, `action`, metadata |

---

## 2. chat/run 공통 로직 구조

### 2.1 구조 원칙

`chat.py`와 `run.py`는 입출력 방식만 다르고, **판단 로직은 모두 `src/agent.py`를 통해 공유**한다.

```text
chat.py
  └─ 사용자 입력 수집
      └─ src.agent.process_ticket(...)

run.py
  └─ ticket JSON 순회
      └─ src.agent.process_ticket(...)

src/agent.py
  ├─ request 정규화
  ├─ safety gate 실행
  ├─ intent classification
  ├─ slot extraction
  ├─ policy application
  ├─ (Q4) cal.com orchestration
  └─ response build
```

### 2.2 공통화 이유

1. **같은 입력에 같은 판단을 보장**해야 한다.  
   인터랙티브 데모와 배치 결과가 다르면 Hidden Test에서 쉽게 드러난다.

2. **안전 규칙을 한 곳에서 관리**해야 한다.  
   의료 상담 거부나 인젝션 차단이 chat/run 중 한쪽에서만 누락되면 치명적이다.

3. **테스트와 유지보수가 단순해진다.**  
   `src/agent.py`만 기준으로 검증하면 두 실행 모드를 동시에 커버할 수 있다.

---

## 3. safety-first 파이프라인 근거

### 3.1 왜 안전성 게이트가 첫 단계인가

과제의 Hard Constraints는 다음을 최우선으로 요구한다.

- 의료 상담 금지
- 목적 외 사용 거부
- 허위 정보 제공 금지

따라서 예약 의도 해석보다 먼저, **응답하면 안 되는 요청을 차단하는 단계**가 선행되어야 한다. 이 구조는 Q3의 “의료 질문 오응답 0건 지향” 요구와도 일치한다.

### 3.2 안전성 게이트가 먼저여야 하는 이유

1. **의료 질문을 분류 이전에 차단**할 수 있다.  
   LLM이 먼저 해석하면 진단/약물/치료 방향으로 과생성할 위험이 있다.

2. **프롬프트 인젝션이 후단 로직에 침투하지 못한다.**  
   내부 프롬프트 공개 요구나 정책 무시 요청은 예약 처리 이전에 차단해야 한다.

3. **응급/급성 통증은 일반 예약 흐름보다 사람 개입이 우선**이다.  
   자동 예약이 아니라 `escalate`로 전환해야 하므로 파이프라인 초반 판정이 적합하다.

### 3.3 혼합 요청 처리 원칙

예: “이 약 먹어도 되나요? 그리고 내일 내과 예약도 하고 싶어요.”  
이 경우 의료 판단 부분은 `reject` 영역으로 처리하고, 예약 가능한 부분만 남겨 후속 예약 안내 또는 `clarify`로 이어간다. 즉, safety gate는 대화를 단순 종료하는 장치가 아니라 **위험한 부분을 선제 제거하는 필터**다.

---

## 4. 결정론 policy + LLM hybrid 근거

### 4.1 역할 분리 원칙

이 Agent는 **정책 판단은 코드**, **언어 해석은 LLM**이 맡는 hybrid 구조를 사용한다.

| 계층 | 담당 역할 |
| --- | --- |
| 결정론 policy (`src/policy.py`) | 24시간 규칙, 3명 정원, 초진/재진 슬롯, 예약 존재 여부, 허용/불가 판정 |
| LLM (`src/classifier.py`, `src/llm_client.py`) | 자연어 의도 해석, 분과 추정, 슬롯 정보 추출, 응답 표현 보조 |

### 4.2 정책을 결정론으로 두는 이유

다음 항목은 숫자/경계값이 명확하므로 자유 생성보다 코드가 적합하다.

- 예약 시각 기준 **정확히 24시간 전** 허용 여부
- 1시간 타임 윈도우당 **정확히 3명** 허용 여부
- 초진 40분 / 재진 30분 적용
- 기존 예약 유무에 따른 modify/cancel/check 성립 여부

이 규칙들을 LLM 자유 추론에 맡기면 동일 입력에도 결과가 흔들릴 수 있다. 따라서 **policy.py에서 결정론적으로 선판정**하고, LLM은 이를 덮어쓰지 못하게 설계한다.

### 4.3 LLM을 함께 쓰는 이유

반대로 사용자 입력은 자유도가 높아 순수 규칙 기반만으로는 일반화가 어렵다.

- “목 아픈데 어느 과로 가야 하나요?” 같은 분과 유도 문장
- “예약 바꾸고 싶은데 수요일 말고 목요일요” 같은 축약 표현
- 날짜/시간 일부만 주어지는 모호한 요청

LLM은 이 언어 다양성을 흡수하는 데 유리하다. 즉, **정책의 정답성은 코드로 고정하고, 해석의 유연성은 LLM으로 보완**한다.

---

## 5. Ollama 로컬 모델(`qwen3-coder:30b`) 선택 이유

### 5.1 선택 이유

1. **로컬 실행 가능성**  
   환자 메시지를 외부 SaaS로 보내지 않고 로컬 환경에서 처리할 수 있어 PoC 단계의 보안 설명이 쉽다.

2. **재현성**  
   모델명과 실행 환경을 고정하면 데모와 배치 실행 결과를 보다 일관되게 재현할 수 있다.

3. **구조화 출력 적합성**  
   과제는 JSON 결과 스키마를 요구하므로, Ollama의 `format='json'` 사용이 적합하다.

4. **한국어 입력 처리**  
   본 과제의 사용자 요청과 정책 문서는 한국어 중심이므로, 한국어 지시 이해력이 중요하다.

5. **코드/구조화 작업 친화성**  
   `qwen3-coder:30b`는 자유 대화보다 구조화된 슬롯 채우기와 지시 추종 작업에 유리한 선택지다.

### 5.2 사용 원칙

- LLM 출력은 반드시 JSON으로 제한한다.
- 허용된 7개 action 외 값은 validator에서 폐기한다.
- 모호하거나 위험하면 과감히 `clarify`, `reject`, `escalate`로 폴백한다.
- 확인되지 않은 정보는 생성하지 않는다.

---

## 6. Q4 cal.com 연동 위치

### 6.1 파이프라인 내 위치

Q4 cal.com 연동은 **정책 검사 이후, 응답 생성 이전**에 위치한다.

이유는 다음과 같다.

1. 안전성 게이트를 통과하지 못한 요청은 외부 API를 호출하면 안 된다.
2. 정책 위반 요청(24시간 위반, 정원 초과 등)은 cal.com 호출 전에 걸러야 한다.
3. `book_appointment`로 확정된 요청만 실제 slot 조회/booking 생성 대상으로 넘겨야 한다.

### 6.2 Q4 처리 흐름

```text
안전성 게이트 통과
  ↓
의도 분류 / 슬롯 추출
  ↓
정책 검사 통과
  ↓
cal.com Event Type 매핑
  ↓
available slots 조회
  ↓
사용자 확인(2단계)
  ↓
booking 생성
  ↓
최종 응답 생성
```

### 6.3 공통 로직 원칙

Q4가 추가되어도 `chat.py`와 `run.py`는 직접 cal.com을 호출하지 않고, 반드시 `src/agent.py`와 `src/calcom_client.py`를 통해 공통 로직을 사용한다.

---

## 7. Hidden Test 대응 전략

### 7.1 과적합 방지 원칙

Hidden Test는 공개 ticket 50건 암기가 아니라, 정책 이해와 일반화 성능을 검증한다. 따라서 다음 원칙을 유지한다.

- 특정 문장 패턴 하드코딩보다 action taxonomy 기반 분류
- 정책 문장을 코드 규칙으로 명시화
- 없는 분과/의사/정책/슬롯을 지어내지 않음

### 7.2 경계값 우선 방어

다음 케이스를 명시적으로 방어해야 한다.

- 정확히 24시간 전 / 24시간 미만
- 1시간 내 3명 / 4명째 요청
- 초진 / 재진 / 미확인
- 기존 예약 없음 + modify/cancel/check
- 당일 신규 예약 일반 케이스
- 당일 신규 예약 + 급성 통증/응급 케이스
- 의료 질문 + 예약 요청이 섞인 복합 문장
- 인젝션 문구가 섞인 예약 요청

### 7.3 출력 계약 엄수

Hidden Test는 의미뿐 아니라 포맷 정합성도 볼 가능성이 높다.

- action은 정확히 7개 값만 사용
- 결과 JSON 키는 과제 예시와 동일하게 유지
- 불확실하면 `clarify`
- 위험하면 `reject` 또는 `escalate`
- confidence / reasoning도 실제 판정 근거 기반으로 생성

### 7.4 회귀 테스트 전략

- safety test: 의료 상담/오프토픽/응급 분기 검증
- policy test: 24시간, 3명, 초진/재진, 기존 예약 여부 검증
- batch schema test: 결과 JSON 키와 enum 일치 여부 검증
- shared-core test: `chat.py`와 `run.py`가 동일 agent core를 호출하는지 검증

---

## 8. 권장 모듈 책임 분리

| 파일 | 책임 |
| --- | --- |
| `src/agent.py` | 전체 오케스트레이션, 공통 진입점 |
| `src/classifier.py` | safety / intent / extraction 관련 LLM 보조 해석 |
| `src/policy.py` | 병원 정책의 결정론 판정 |
| `src/llm_client.py` | Ollama 호출 및 JSON 출력 강제 |
| `src/calcom_client.py` | Q4 slot 조회 및 booking 생성 |
| `src/response_builder.py` | 최종 사용자 응답과 배치 JSON 구성 |
| `chat.py` | 인터랙티브 CLI 어댑터 |
| `run.py` | 배치 입출력 어댑터 |

---

## 9. 결론

코비메디 Q2 Agent의 핵심은 “LLM이 전부 판단하는 챗봇”이 아니라, **안전성 게이트와 결정론 정책을 중심으로 LLM을 제한적으로 사용하는 예약 처리 파이프라인**이다. 이 구조는 과제의 Hard Constraints를 우선 충족하면서도, 인터랙티브 데모와 배치 처리에서 동일한 판단 일관성을 확보한다.

특히 `safety-first`, `shared agent core`, `deterministic policy + LLM hybrid`, `local Ollama model`, `policy 후 cal.com 연동`이라는 다섯 가지 축은 Hidden Test와 실제 운영 리스크를 동시에 방어하기 위한 설계 골격이다.