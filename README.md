# 코비메디 예약 챗봇 PoC

서울 소재 중형 네트워크 병원 **코비메디**(3개 분원, 가상)의 진료 예약 접수/변경/취소 업무를 자동화하는 AI Agent PoC.

> *"간단한 예약 문의는 AI 챗봇이 처리하고, 복잡한 건만 사람이 하면 좋겠습니다."*
> — 코비메디 원무과장

## 과제 개요

매일 약 400건의 전화 예약 문의를 CS 인력 3명이 처리하는 상황에서, 단순 예약 업무를 AI로 자동화하는 PoC를 설계하고 작동하는 프로토타입을 구현한다.

### 과제 구성

| 구분 | 내용 | 산출물 |
|------|------|--------|
| Q1 | PoC 성공 지표 제안 | [docs/q1_metric_rubric.md](docs/q1_metric_rubric.md) |
| Q2 | 예약 Agent 구현 (인터랙티브 + 배치) | `chat.py`, `run.py`, `src/` |
| Q3 | 안전성 대응 방안 | [docs/q3_safety.md](docs/q3_safety.md) |
| Q4 | cal.com 연동 (선택) | `src/calcom_client.py` |
| 통합 | 최종 리포트 | [docs/final_report.md](docs/final_report.md) |

### 두 가지 실행 모드

**모드 1: 인터랙티브 데모** (`chat.py`) — 사용자가 메시지를 입력하면 실시간으로 응답하는 대화형 인터페이스.

```
🏥 코비메디 예약 챗봇입니다. 무엇을 도와드릴까요?

> 내일 오후 2시에 이비인후과 예약하고 싶습니다
예약하시는 분이 환자 본인이신가요, 아니면 가족이나 지인을 대신하여 예약하시는 건가요?

> 본인이에요
동명이인 확인을 위해 휴대전화 번호를 알려주세요.

> 010-1234-5678
내일 14시 이비인후과 이춘영 원장님 진료 예약을 도와드리겠습니다. 예약을 진행할까요?

> 네
예약이 완료되었습니다!
```

**모드 2: 배치 처리** (`run.py`) — tickets.json을 입력받아 각 티켓의 의도 분류 + 응답을 JSON으로 출력.

```json
{
  "ticket_id": "T-001",
  "classified_intent": "book_appointment",
  "department": "이비인후과",
  "action": "book_appointment",
  "response": "김민수님, 내일(3/16) 오후 2시 이비인후과 이춘영 원장님 진료 예약을 도와드리겠습니다.",
  "confidence": 0.95,
  "reasoning": "재진 환자, 분과/날짜/시간 명시, 정책 위반 없음"
}
```

두 모드는 **동일한 에이전트 로직**(`src/agent.py`)을 공유한다.

### 핵심 설계 결정

**1. 본인/대리인 확인을 가장 먼저 수행** — 예약자 이름만으로는 실제 환자인지 알 수 없다. 예약 의도가 파악되면 날짜/시간보다 먼저 "본인이신가요?"를 물어 전화번호를 진실원천(Source of Truth)으로 확보한다. 대리인 이름으로 예약이 확정되는 치명적 오류(-$500)를 방지.

**2. LLM 위임을 완전히 배제한 결정론적 정책 엔진** — "1시간당 최대 3명", "24시간 이전만 취소 가능" 같은 산술 규칙을 LLM 프롬프트에 맡기면 할루시네이션이 발생한다. LLM은 자연어 이해만 담당하고, 예약 허용 여부는 `policy.py`의 Python 코드로만 판정.

**3. Safety Gate 최우선 배치** — 의료 상담 오답률 0%를 달성하기 위해, LLM이 답변을 생성하기 전에 규칙 기반 가드레일이 먼저 작동한다. 의료 키워드가 감지되면 LLM을 아예 거치지 않고 하드코딩된 안전 문구를 출력(Fast-path).

### KPI 지표

| 지표 | 목표 | 의미 |
|------|------|------|
| 안전 종결률 | >= 70% | 하드 실패 없이 챗봇이 안전하게 대화를 마친 비율 |
| 완전 자동화 성공률 | >= 45% | 상담원 개입 없이 예약을 확정한 비율 |
| 의료 상담 오답률 | **0.0%** | 의료법 위반 소지가 있는 답변 — 단 1건도 불가 |
| 치명적 실패율 | < 1.0% | 거짓 예약 확정, 정책 위반 예약 강행 |

비용 구조: 성공 +$10, 소프트 실패 -$20, **하드 실패 -$500** (1건이 성공 50건을 상쇄).

---

## 과제 요구사항 대비 구현 현황

### 필수 요구사항 (Q1~Q3)

| 요구사항 | 세부 항목 | 상태 | 구현 파일 / 산출물 |
|---------|----------|------|-------------------|
| **Q1: Metric Rubric** | 성공 지표 KPI 2~3개 (목표 수치) | 완료 | [docs/q1_metric_rubric.md](docs/q1_metric_rubric.md) |
| | 안전/제약 지표 1~2개 (임계값) | 완료 | 의료 오답률 0%, 치명적 실패율 <1% |
| | 지표별 선정 근거 + 측정 방법 | 완료 | 비용 구조 분석 기반 |
| **Q2: Agent 구현** | 모드 1 — 인터랙티브 데모 (`chat.py`) | 완료 | `chat.py` → `src/agent.py` |
| | 모드 2 — 배치 처리 (`run.py`) | 완료 | `run.py` → `src/agent.py` |
| | 두 모드가 동일한 Agent 로직 공유 | 완료 | `chat.py`와 `run.py` 모두 `src/agent.py`의 `process_ticket()` 호출 |
| | 진료 예약 정책 위반 여부 판단 | 완료 | `src/policy.py` — 운영시간, 정원, 24시간 룰, 초진/재진 |
| | 의료 상담 / 목적 외 사용 거부 | 완료 | `src/classifier.py` — Safety Gate (규칙 기반 + LLM 폴백) |
| | 모호한 요청에 clarification 응답 | 완료 | `src/agent.py` — pending_missing_info 큐, clarify_turn_count |
| | 배치 출력 JSON 스키마 준수 | 완료 | ticket_id, classified_intent, department, action, response, confidence, reasoning |
| **Q2: 데모 증빙** | 정상 예약 처리 스크린샷 | 완료 | [docs/demo_evidence.md](docs/demo_evidence.md) |
| | 의료 상담 거부 스크린샷 | 완료 | 동일 문서 |
| | 모호한 요청 → clarification 스크린샷 | 완료 | 동일 문서 |
| **Q3: 안전성 대응** | 실패율 0% 달성의 기술적 의미 | 완료 | [docs/q3_safety.md](docs/q3_safety.md) |
| | 기술적 가드레일 + 프로세스 | 완료 | Safety Gate Fast-path, LLM 생성 배제, 혼합 요청 보수적 차단 |
| | Metric Rubric 변경 필요 시 수정안 | 완료 | 의료 오답률 0% 지표 추가 |
| | 잔존 리스크 + 고객 커뮤니케이션 | 완료 | 동일 문서 |
| **리포트** | Q1~Q3 통합 리포트 1부 | 완료 | [docs/final_report.md](docs/final_report.md) |
| | AI 도구 활용 내역 | 완료 | 동일 문서 §6 |

### 선택 요구사항 (Q4: cal.com 연동)

| 요구사항 | 세부 항목 | 상태 | 구현 파일 / 산출물 |
|---------|----------|------|-------------------|
| **Q4: cal.com** | 3개 Event Type 설정 (이비인후과/내과/정형외과) | 완료 | `.env` — CALCOM_ENT_ID, CALCOM_INTERNAL_ID, CALCOM_ORTHO_ID |
| | available slots API로 가용 시간 조회 | 완료 | `src/calcom_client.py` — `get_available_slots()` |
| | 고객에게 가용 시간 공유 응답 | 완료 | `src/agent.py` — 선제적 슬롯 안내 (시간 미입력 시) |
| | 실제 booking 생성 | 완료 | `src/calcom_client.py` — `create_booking()` |
| | 1시간당 3명 정책 적용 | 완료 | 로컬 `policy.py` + Cal.com slot availability 이중 검증 |
| | 거짓 성공 방지 (API 실패 시) | 완료 | Cal.com 실패 → 로컬 저장 차단, AGENT_HARD_FAIL 기록 |
| | Graceful Degradation (API 키 미설정) | 완료 | `is_calcom_enabled()` — 로컬 정책만으로 정상 동작 |

### 예약 정책 구현 현황

| 정책 규칙 | 상태 | 구현 위치 | 테스트 |
|-----------|------|----------|--------|
| 예약에 분과 + 날짜 + 시간 필수 | 완료 | `src/agent.py` — missing_info 큐 | 시나리오 1-2, 8-2 |
| 1시간당 최대 3명 | 완료 | `src/policy.py` — `is_slot_available()` | 시나리오 3-2, 3-3 |
| 초진 40분 / 재진 30분 슬롯 | 완료 | `src/policy.py` — `get_appointment_duration()` | 시나리오 3-1 |
| 평일 09:00-18:00 | 완료 | `src/policy.py` — `is_within_operating_hours()` | 시나리오 7-7, 7-8, 7-9 |
| 토요일 09:00-13:00 | 완료 | 동일 | 시나리오 7-5, 7-6 |
| 일요일/공휴일 휴진 | 완료 | 동일 | 시나리오 7-4 |
| 점심시간 12:30-13:30 예약 불가 | 완료 | 동일 | 시나리오 7-1, 7-2, 7-3 |
| 변경/취소 24시간 전까지 | 완료 | `src/policy.py` — `is_change_or_cancel_allowed()` | 시나리오 4-1 ~ 4-4 |
| 대리 예약 시 환자 이름 + 연락처 확인 | 완료 | `src/agent.py` — proxy 식별 흐름 | 시나리오 2-1 ~ 2-4 |
| 증상 기반 분과 안내 (진단 아닌 안내) | 완료 | `src/classifier.py` — department_hint | 시나리오 6-2 |
| 의료 상담 절대 금지 | 완료 | `src/classifier.py` — Safety Gate | 시나리오 5-1 ~ 5-7 |
| 프롬프트 인젝션 거부 | 완료 | `src/classifier.py` — INJECTION_PATTERNS | 시나리오 5-5 |
| 개인정보 보호 (타 환자 정보 차단) | 완료 | `src/classifier.py` — PRIVACY_REQUEST_PATTERNS | 시나리오 5-2 |
| 에스컬레이션 (응급/불만/보험) | 완료 | `src/classifier.py` — EMERGENCY/COMPLAINT/OPERATIONAL 패턴 | 시나리오 5-3, 5-7 |

---

## 설치 및 실행 가이드

GitHub에서 clone한 후 `chat.py` 또는 `run.py`를 실행하기까지의 전체 과정이다.

### 사전 요구 사항

| 항목 | 버전 | 용도 | 필수 여부 |
|------|------|------|----------|
| Python | 3.12 이상 | 에이전트 런타임 | 필수 |
| Ollama | 0.4.0 이상 | 로컬 LLM 서빙 | 필수 |
| Git | - | 저장소 clone | 필수 |
| Cal.com 계정 | - | 외부 예약 시스템 연동 (Q4) | 선택 |

### Step 1: 저장소 clone

```bash
git clone https://github.com/<owner>/kobimedi-poc.git
cd kobimedi-poc
```

### Step 2: Python 가상환경 생성 + 의존성 설치

```bash
python3 -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

`requirements.txt` 내용:

```
ollama>=0.4.0
pytest>=7.0.0
freezegun>=1.2.0
requests>=2.31.0
python-dotenv>=1.0.0
```

### Step 3: Ollama 설치 + LLM 모델 다운로드

Ollama가 설치되어 있지 않다면:

```bash
# macOS
brew install ollama

# Linux
curl -fsSL https://ollama.com/install.sh | sh
```

LLM 모델 다운로드 (약 18GB, 최초 1회):

```bash
ollama pull qwen3-coder:30b
```

Ollama 서비스 구동 확인:

```bash
ollama list
# NAME                ID              SIZE
# qwen3-coder:30b     06c1097efce0    18 GB
```

### Step 4: 환경변수 설정 (.env)

Cal.com 연동(Q4)을 사용하려면 `.env` 파일을 프로젝트 루트에 생성한다. Cal.com 연동이 불필요하면 이 단계를 건너뛰어도 된다 (Graceful Degradation으로 로컬 정책만으로 동작).

```bash
# .env (프로젝트 루트)
CALCOM_API_KEY=cal_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxx

# cal.com 분과별 Event Type ID
CALCOM_ENT_ID=1234567        # 이비인후과
CALCOM_INTERNAL_ID=1234568   # 내과
CALCOM_ORTHO_ID=1234569      # 정형외과
```

Cal.com Event Type ID는 cal.com 대시보드 > Event Types에서 확인할 수 있다.

### Step 5: 설치 확인

```bash
# 자동 환경 점검 (가상환경, 의존성, Ollama 모델 상태 확인)
./scripts/init.sh
```

또는 수동 확인:

```bash
# Python 버전
python --version       # 3.12 이상

# Ollama 모델
ollama list            # qwen3-coder:30b 확인

# 유닛 테스트
pytest tests/ -v       # 226 passed
```

### Step 6: 실행

```bash
# 인터랙티브 챗봇
python chat.py

# 배치 처리
python run.py --input data/tickets.json --output results.json
```

### 빠른 시작 (한 줄 요약)

위 과정을 이미 아는 경우:

```bash
git clone <repo> && cd kobimedi-poc
./scripts/init.sh
python chat.py
```

### 문제 해결

| 증상 | 원인 | 해결 |
|------|------|------|
| `ModuleNotFoundError: No module named 'ollama'` | 의존성 미설치 | `pip install -r requirements.txt` |
| `ollama._types.ResponseError` | Ollama 서비스 미구동 | `ollama serve` 실행 후 재시도 |
| `model "qwen3-coder:30b" not found` | 모델 미다운로드 | `ollama pull qwen3-coder:30b` |
| Cal.com 관련 clarify 응답 | `.env` 미설정 | `.env` 파일 생성 또는 무시 (로컬만으로 동작) |
| `pytest` 시 226개 미만 통과 | 환경 문제 | `pip install -r requirements.txt` 재실행 |

## 프로젝트 구조

```
kobimedi-poc/
├── chat.py                  # 인터랙티브 챗봇 (모드 1)
├── run.py                   # 배치 처리 (모드 2)
├── src/
│   ├── agent.py             # 핵심 에이전트 파이프라인
│   ├── classifier.py        # Safety gate + 의도 분류 (Ollama)
│   ├── policy.py            # 결정론적 정책 엔진
│   ├── storage.py           # bookings.json 저장소
│   ├── calcom_client.py     # Cal.com API v2 연동 (Q4)
│   ├── response_builder.py  # 응답 생성
│   ├── llm_client.py        # Ollama LLM 래퍼
│   ├── models.py            # 데이터 모델 (Action, Booking, Ticket 등)
│   └── metrics.py           # KPI 이벤트 기록
├── scripts/                 # 운영 스크립트
├── tests/                   # 유닛 테스트 (226개, Mock 기반)
├── data/
│   ├── tickets.json         # 입력 티켓 50건
│   └── bookings.json        # 예약 저장소 (진실원천)
└── docs/
    ├── final_report.md          # 최종 리포트 (Q1~Q4 통합)
    ├── q1_metric_rubric.md      # Q1: PoC 성공 지표 제안서
    ├── q3_safety.md             # Q3: 안전성 대응 방안
    ├── architecture.md          # 에이전트 아키텍처 설계 문서
    ├── policy_digest.md         # 진료 예약 정책 요약
    ├── demo_evidence.md         # 인터랙티브 데모 증빙
    ├── test_scenarios.md        # 테스트 시나리오 명세 (51개, 9개 카테고리)
    ├── test_results_unit.txt    # 유닛 테스트 실행 결과
    └── test_results_scenario.txt # 시나리오 테스트 실행 결과
```

## 시스템 아키텍처

### 파이프라인 흐름

사용자 메시지가 입력되면 아래 순서로 처리된다. 각 단계는 독립적이며, 앞 단계에서 차단되면 뒤 단계는 실행되지 않는다.

```
사용자 발화
  │
  ▼
┌─────────────────────────────────────────────────────────┐
│ 1. Safety Gate  (classifier.py)                         │
│    규칙 기반 패턴 매칭 → LLM 폴백                        │
│    의료 상담 → reject                                    │
│    프롬프트 인젝션 → reject                               │
│    응급/불만 → escalate                                  │
│    비용/보험 → escalate                                  │
│    개인정보 요청 → reject                                 │
│    미지원 분과/의사 → reject                              │
│    증상 기반 분과 안내 → clarify (진단 아닌 안내)           │
└──────────────┬──────────────────────────────────────────┘
               │ safe
               ▼
┌─────────────────────────────────────────────────────────┐
│ 2. 의도 분류 + 정보 추출  (classifier.py → Ollama LLM)   │
│    action: book / modify / cancel / check / clarify      │
│    추출: 분과, 날짜, 시간, 환자명, 연락처, 대리 여부       │
└──────────────┬──────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────┐
│ 3. 대화 상태 병합  (agent.py)                            │
│    채팅 모드: session_state에 누적 슬롯 병합              │
│    is_proxy_booking → patient_name → patient_contact     │
│    → department → date → time 순서로 수집                │
│    4회 clarify 실패 → escalate                           │
└──────────────┬──────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────┐
│ 4. 저장소 조회  (storage.py → bookings.json)             │
│    전화번호 우선 환자 식별                                │
│    초진/재진 판정 (저장소가 진실원천)                      │
│    기존 예약 조회 (변경/취소/확인 시)                      │
└──────────────┬──────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────┐
│ 5. 정책 검사  (policy.py) — 결정론, LLM 위임 금지        │
│    운영시간: 평일 09-18, 토 09-13, 일 휴진, 점심 차단     │
│    정원: 1시간당 최대 3명                                 │
│    겹침: 초진 40분 / 재진 30분 슬롯 충돌 감지             │
│    24시간 룰: 변경/취소는 예약 24시간 전까지               │
│    대안 슬롯: 거절 시 같은 날 1-3개 제안                  │
└──────────────┬──────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────┐
│ 6. Cal.com 연동  (calcom_client.py) — Q4 선택            │
│    가용 슬롯 교차 검증 (확인 질문 전)                     │
│    예약 생성 (사용자 확인 후)                              │
│    실패 시 로컬 저장 차단 (거짓 성공 방지)                 │
│    API 미설정 시 로컬만으로 정상 동작 (Graceful Degradation)│
└──────────────┬──────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────┐
│ 7. 영속화 + 응답  (storage.py → response_builder.py)     │
│    bookings.json 원자적 저장 (temp + rename)             │
│    확인 질문 / 성공 메시지 / clarify / reject 생성        │
│    confidence + reasoning 동적 계산                       │
└─────────────────────────────────────────────────────────┘
```

### 모듈 의존성

```
chat.py ──┐
           ├──▶ src/agent.py (공유 핵심 로직)
run.py  ──┘         │
                     ├──▶ src/classifier.py ──▶ src/llm_client.py ──▶ Ollama
                     ├──▶ src/policy.py (순수 산술, 외부 의존 없음)
                     ├──▶ src/storage.py ──▶ data/bookings.json
                     ├──▶ src/calcom_client.py ──▶ Cal.com API v2
                     ├──▶ src/response_builder.py
                     └──▶ src/metrics.py
```

### Action 분류 체계

에이전트는 모든 요청을 아래 7개 action 중 하나로 판정한다.

| Action | 설명 | 예시 |
|--------|------|------|
| `book_appointment` | 신규 예약 | "내일 오후 2시 이비인후과 예약" |
| `modify_appointment` | 기존 예약 변경 | "수요일 예약을 목요일로 변경" |
| `cancel_appointment` | 기존 예약 취소 | "금요일 예약 취소해주세요" |
| `check_appointment` | 예약 확인/조회 | "다음 주 예약 확인해주세요" |
| `clarify` | 정보 부족, 추가 확인 | "예약하고 싶어요" (날짜/시간/분과 누락) |
| `escalate` | 상담원 연결 | 급성 통증, 반복 불만, 보험/비용 문의 |
| `reject` | 목적 외 사용 거부 | 의료 상담, 잡담, 프롬프트 인젝션 |

---

## 스크립트 사용법

### `scripts/init.sh` — 환경 초기화

프로젝트 최초 세팅 시 실행. 가상환경 생성, 의존성 설치, Ollama 모델 상태 확인.

```bash
./scripts/init.sh
```

### `scripts/check.sh` — 전체 검증

구문 검사, Feature 통과율, 유닛 테스트, 배치 처리, Gold 평가를 한 번에 실행.

```bash
./scripts/check.sh
```

### `scripts/run_tests.sh` — 테스트 실행기

유닛 테스트와 시나리오 테스트를 선택적으로 실행하고 결과 파일을 자동 생성한다.

```bash
./scripts/run_tests.sh              # 유닛 테스트만 (~9초)
./scripts/run_tests.sh --scenario   # 시나리오 테스트만 (51개, ~80초)
./scripts/run_tests.sh --all        # 유닛 + 시나리오 전체
```

| 옵션 | 대상 | 결과 파일 |
|------|------|----------|
| (기본) | 유닛 테스트 | `docs/test_results_unit.txt` |
| `--scenario` | 시나리오 테스트 51개 (실제 Ollama + Cal.com) | `docs/test_results_scenario.txt` |
| `--all` | 위 전체 | 2개 파일 모두 |

### `scripts/run_scenario_tests.py` — 시나리오 테스트 러너

`docs/test_scenarios.md`에 정의된 51개 시나리오를 실제 LLM으로 대화 흐름대로 실행하고, 턴별 결과를 상세히 리포트한다.

```bash
# 전체 9개 카테고리
python scripts/run_scenario_tests.py

# 특정 카테고리만
python scripts/run_scenario_tests.py --category 1    # Happy Path
python scripts/run_scenario_tests.py --category 5    # Safety Gate

# LLM 없이 정책 엔진만 (카테고리 3, 4, 7)
python scripts/run_scenario_tests.py --policy-only

# 결과를 파일에 저장
python scripts/run_scenario_tests.py --output docs/test_results_scenario.txt
```

| 카테고리 | 테스트 수 | LLM 필요 |
|---------|----------|---------|
| 1. 정상 예약 완료 | 4 | O |
| 2. 환자 식별 & 대리 | 4 | O |
| 3. 정책 엔진 슬롯 계산 | 5 | X |
| 4. 24시간 변경/취소 | 5 | X |
| 5. Safety Gate | 7 | O |
| 6. 분과/운영시간 | 3 | O |
| 7. 운영시간 정책 (F-052) | 12 | X |
| 8. 대화 상태 관리 | 3 | O |
| 9. Cal.com 외부 연동 | 8 | O |

### `scripts/cleanup_bookings.py` — Cal.com 예약 일괄 삭제 + 로컬 동기화

테스트 과정에서 생성된 Cal.com 원격 예약과 로컬 bookings.json을 한 번에 정리한다.

```bash
# 삭제 대상만 확인 (실제 삭제 안 함)
python scripts/cleanup_bookings.py --dry-run

# 전체 삭제 실행 (확인 프롬프트)
python scripts/cleanup_bookings.py

# 확인 없이 즉시 실행
python scripts/cleanup_bookings.py --force

# 로컬 bookings.json만 초기화 (Cal.com 안 건드림)
python scripts/cleanup_bookings.py --local-only
```

**동작 흐름:**
1. Cal.com `GET /bookings` → 원격 예약 목록 조회
2. 각 예약에 `POST /bookings/{uid}/cancel` → 원격 취소
3. 로컬 `data/bookings.json` → `[]` 초기화

## 테스트

### 테스트 파일 목록

총 10개 테스트 파일, **226개 유닛 테스트** + 시나리오 러너 51개.

| 파일 | 테스트 수 | 검증 대상 |
|------|----------|----------|
| `tests/test_scenarios.py` | 51 | 9개 카테고리 시나리오 (Happy Path, Identity, Policy, 24h, Safety, Department, Operating Hours, Dialogue, Cal.com) |
| `tests/test_calcom.py` | 51 | Cal.com API 연동 (슬롯 조회, 예약 생성, 취소, Race Condition, Graceful Degradation) |
| `tests/test_safety.py` | 35 | Safety gate (의료 상담 차단, 인젝션 방어, 증상 안내, 혼합 요청 분리, LLM 폴백) |
| `tests/test_response_builder.py` | 27 | 응답 메시지 생성 (proxy 질문, 이름/연락처 수집, 분과/시간 안내) |
| `tests/test_classifier.py` | 20 | 의도 분류 (LLM 파싱, 에러 복구, 의사→분과 매핑, 증상→분과 매핑) |
| `tests/test_policy.py` | 14 | 정책 엔진 (슬롯 겹침, 정원, 24시간 룰, 대안 슬롯, 초진/재진 시간) |
| `tests/test_dialogue.py` | 13 | 멀티턴 대화 (proxy 수집, 상태 유지, clarify 에스컬레이션, 배치/채팅 분기) |
| `tests/test_storage.py` | 11 | 저장소 (영속화, 중복 방지, 취소, 초진/재진 판정, 파일 손상 복구) |
| `tests/test_generalization.py` | 3 | 일반화 (한국어 인젝션, 혼합 요청, 모호한 환자 유형) |
| `tests/test_batch.py` | 1 | 배치 모드 (run.py 출력 스키마 + KPI 메트릭) |

### 2가지 테스트 레벨

| 레벨 | 설명 | LLM | 속도 |
|------|------|-----|------|
| **유닛 테스트** (226개) | Mock 기반, 각 컴포넌트 격리 검증 | Mock | ~9초 |
| **시나리오 테스트** (51개) | 실제 Ollama + Cal.com + Storage, 대화 흐름 검증 | 실제 호출 | ~80초 |

시나리오 테스트는 `scripts/run_scenario_tests.py`로 실행하며, 각 턴마다 사용자 발화 → 챗봇 응답 → action → 상태 변화를 상세히 출력한다. exit code로 PASS/FAIL을 판정하므로 CI에서도 사용 가능하다.

### 실행 환경 요구 사항

| 항목 | 유닛 테스트 | 시나리오 테스트 |
|------|-----------|--------------|
| Python 3.12+ | 필수 | 필수 |
| Ollama + qwen3-coder:30b | 불필요 | 필수 |
| .env (CALCOM_API_KEY) | 불필요 | Cal.com 관련 시나리오만 |

### 시나리오 명세서

모든 시나리오의 사용자 발화, 실행 의도, 기대 결과는 아래 문서에 정리되어 있다.

- [docs/test_scenarios.md](docs/test_scenarios.md) — 51개 시나리오 명세 (9개 카테고리)

## 도메인 정보

| 분과 | 담당 의사 | 기본 슬롯 |
|------|----------|----------|
| 이비인후과 | 이춘영 원장 | 30분 |
| 내과 | 김만수 원장 | 30분 |
| 정형외과 | 원징수 원장 | 30분 |

### 핵심 예약 정책

- 1시간 타임 윈도우당 최대 3명
- 초진 환자 40분 슬롯 (재진 30분)
- 변경/취소는 예약 시간 24시간 전까지만 가능
- 평일 09:00-18:00, 토 09:00-13:00, 일 휴진
- 점심시간 12:30-13:30 예약 불가
- 의료 상담/진단/약물 추천 절대 금지

## AI 도구 활용 내역

| 도구 | 용도 |
|------|------|
| Claude Code (Anthropic CLI) | 아키텍처 설계, 구현, 테스트 작성, 코드 리뷰 |
| Ollama + qwen3-coder:30b | 챗봇 LLM (Safety gate, 의도 분류, 정보 추출) |
| Cal.com API v2 | 외부 예약 시스템 연동 (Q4) |
