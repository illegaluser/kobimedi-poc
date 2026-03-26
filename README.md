# 코비메디 예약 챗봇 PoC

서울 소재 중형 네트워크 병원 **코비메디**(3개 분원)의 진료 예약 접수/변경/취소를 자동화하는 AI Agent.

> *"간단한 예약 문의는 AI 챗봇이 처리하고, 복잡한 건만 사람이 하면 좋겠습니다."* — 코비메디 원무과장

```bash
# 빠른 시작
./scripts/init.sh && python chat.py
```

---

## 1. 과제 개요

매일 400건의 전화 예약을 CS 3명이 처리하는 상황에서, 단순 예약을 AI로 자동화하는 PoC.

하드 실패 1건(-$500)이 성공 50건(+$500)을 상쇄하므로, **안전성(의료 오답 0%)을 최우선**으로 설계했다.

---

## 2. 요구사항 대비 구현 현황

### 제출물 (Deliverables)

| 제출물 | 요구사항 | 상태 | 파일 |
|--------|---------|------|------|
| Q1: Metric Rubric | 성공 KPI 2~3개 + 안전 지표 1~2개 (1페이지) | 완료 | [q1_metric_rubric.md](docs/q1_metric_rubric.md) |
| Q2: Agent 아키텍처 | 설계 설명 + 주요 결정 근거 | 완료 | [final_report.md](docs/final_report.md) §3 |
| Q2: 인터랙티브 데모 | `python chat.py` 실행 가능 | 완료 | [chat.py](chat.py) |
| Q2: 배치 처리 | `python run.py --input tickets.json --output results.json` | 완료 | [run.py](run.py) |
| Q2: 데모 증빙 | 정상 예약 / 의료 거부 / clarification 3개 시나리오 | 완료 | [demo_evidence.md](docs/demo_evidence.md) |
| Q3: 안전성 대응 | 의료 오답 0% 방안 (1~2페이지) | 완료 | [q3_safety.md](docs/q3_safety.md) |
| AI 도구 내역 | 사용 도구 + 활용 패턴 | 완료 | [final_report.md](docs/final_report.md) §6 |
| Q4: cal.com 연동 (선택) | 실제 예약 생성 + API 연동 | 완료 | [calcom_client.py](src/calcom_client.py) |

### Agent 공통 요건

| 요구사항 | 구현 | 검증 |
|---------|------|------|
| 7개 Action 분류 (book/modify/cancel/check/clarify/escalate/reject) | `src/classifier.py` + `src/agent.py` | [전체 9개 카테고리 51개 시나리오](docs/test_scenarios.md) |
| 두 모드가 동일한 Agent 로직 공유 | `chat.py`, `run.py` 모두 `src/agent.py`의 `process_ticket()` 호출 | `test_dialogue.py::test_F048` |
| 진료 예약 정책 위반 판단 | `src/policy.py` (결정론, LLM 위임 금지) | [3. 정책엔진](docs/test_scenarios.md#3-정책-엔진-슬롯-계산-deterministic-policy)(3-1~3-5), [4. 24시간룰](docs/test_scenarios.md#4-24시간-변경취소-규칙-modification--cancellation)(4-1~4-5), [7. 운영시간](docs/test_scenarios.md#7-운영시간-정책-operating-hours-f-052)(7-1~7-12) |
| 의료 상담 / 목적 외 사용 거부 | `src/classifier.py` Safety Gate (규칙 기반 + LLM 폴백) | [5. Safety Gate](docs/test_scenarios.md#5-safety-gate-safety--clarification)(5-1~5-7) |
| 모호한 요청에 clarification | `src/agent.py` pending_missing_info 큐 | [1. Happy Path](docs/test_scenarios.md#1-정상-예약-완료-happy-path)(1-2~1-4), [8. 대화상태](docs/test_scenarios.md#8-대화-상태-관리-dialogue-state-machine)(8-1~8-3) |
| 배치 출력 JSON 스키마 (ticket_id, classified_intent, department, action, response, confidence, reasoning) | `src/agent.py` `_build_response_and_record()` | `test_batch.py` |
| Hidden Test 일반화 대비 | tickets.json 50건 과적합 방지 — 정책 기반 결정론 설계 | `test_generalization.py` |

### 예약 정책 구현

| 정책 | 구현 | 테스트 시나리오 |
|------|------|---------------|
| 예약에 분과 + 날짜 + 시간 필수 | `agent.py` missing_info 큐 | [1. Happy Path](docs/test_scenarios.md#1-정상-예약-완료-happy-path) 1-2, [8. 대화상태](docs/test_scenarios.md#8-대화-상태-관리-dialogue-state-machine) 8-2 |
| 1시간당 최대 3명 | `policy.py` `is_slot_available()` | [3. 정책엔진](docs/test_scenarios.md#3-정책-엔진-슬롯-계산-deterministic-policy) 3-2, 3-3 |
| 초진 40분 / 재진 30분 | `policy.py` `get_appointment_duration()` | [3. 정책엔진](docs/test_scenarios.md#3-정책-엔진-슬롯-계산-deterministic-policy) 3-1 |
| 평일 09:00-18:00 | `policy.py` `is_within_operating_hours()` | [7. 운영시간](docs/test_scenarios.md#7-운영시간-정책-operating-hours-f-052) 7-7~7-9 |
| 토요일 09:00-13:00 | 동일 | [7. 운영시간](docs/test_scenarios.md#7-운영시간-정책-operating-hours-f-052) 7-5, 7-6 |
| 일요일 휴진 | 동일 | [7. 운영시간](docs/test_scenarios.md#7-운영시간-정책-operating-hours-f-052) 7-4 |
| 점심 12:30-13:30 불가 | 동일 | [7. 운영시간](docs/test_scenarios.md#7-운영시간-정책-operating-hours-f-052) 7-1~7-3 |
| 변경/취소 24시간 전까지 | `policy.py` `is_change_or_cancel_allowed()` | [4. 24시간룰](docs/test_scenarios.md#4-24시간-변경취소-규칙-modification--cancellation) 4-1~4-4 |
| 대리 예약 시 환자 이름 + 연락처 확인 | `agent.py` proxy 식별 흐름 | [2. 환자식별](docs/test_scenarios.md#2-환자-식별--대리-예약-identity--proxy) 2-1~2-4 |
| 증상 → 분과 안내 (진단 아닌 안내) | `classifier.py` department_hint | [6. 분과](docs/test_scenarios.md#6-분과-및-운영시간-department--hours) 6-2 |
| 의료 상담 절대 금지 | `classifier.py` Safety Gate | [5. Safety](docs/test_scenarios.md#5-safety-gate-safety--clarification) 5-1 |
| 프롬프트 인젝션 거부 | `classifier.py` INJECTION_PATTERNS | [5. Safety](docs/test_scenarios.md#5-safety-gate-safety--clarification) 5-5 |
| 개인정보 보호 | `classifier.py` PRIVACY_REQUEST_PATTERNS | [5. Safety](docs/test_scenarios.md#5-safety-gate-safety--clarification) 5-2 |
| 에스컬레이션 (응급/불만/보험/의사 연락처) | `classifier.py` 패턴 매칭 | [5. Safety](docs/test_scenarios.md#5-safety-gate-safety--clarification) 5-3, 5-7 |
| 슬롯 만석 시 대안 시간 안내 | `policy.py` `suggest_alternative_slots()` | [3. 정책엔진](docs/test_scenarios.md#3-정책-엔진-슬롯-계산-deterministic-policy) 3-3 |
| 허위 정보 금지 (거짓 성공 방지) | Cal.com 실패 시 로컬 저장 차단 | [9. Cal.com](docs/test_scenarios.md#9-q4-calcom-외부-연동--장애-복구-external-integration) 9-2, 9-8 |

### Q4: cal.com 연동

| 요구사항 | 구현 |
|---------|------|
| 3개 Event Type 설정 | `.env` — ENT_ID, INTERNAL_ID, ORTHO_ID |
| available slots API 조회 | `calcom_client.py` `get_available_slots()` |
| 가용 시간 공유 응답 | `agent.py` 선제적 슬롯 안내 |
| 실제 booking 생성 | `calcom_client.py` `create_booking()` |
| API 미설정 시 정상 동작 | `is_calcom_enabled()` Graceful Degradation |

---

## 3. 핵심 설계 결정

**왜 LLM에 예약 판단을 맡기지 않는가?**

| 결정 | 이유 |
|------|------|
| Safety Gate를 파이프라인 최상단에 배치 | LLM이 답변을 생성하기 전에 의료 질문을 차단해야 오답률 0% 달성 가능 |
| 정책 엔진을 Python 코드로 구현 | "1시간 3명", "24시간 룰" 같은 산술 규칙을 LLM에 맡기면 할루시네이션 발생 |
| 본인/대리인 확인을 가장 먼저 수행 | 전화번호를 진실원천으로 확보해야 동명이인 식별 가능, 대리인 이름으로 예약 확정 방지 |

---

## 4. 시스템 아키텍처

```
사용자 발화
  │
  ▼
┌─────────────────────────────────────────────────────┐
│ 1. Safety Gate (classifier.py)                      │
│    규칙 기반 패턴 → LLM 폴백                         │
│    의료/인젝션/잡담 → reject                         │
│    응급/불만/보험 → escalate                         │
└──────────────┬──────────────────────────────────────┘
               │ safe
               ▼
┌─────────────────────────────────────────────────────┐
│ 2. 의도 분류 + 정보 추출 (classifier.py → Ollama)    │
│    7개 action 판정 + 분과/날짜/시간/환자 정보 추출    │
└──────────────┬──────────────────────────────────────┘
               ▼
┌─────────────────────────────────────────────────────┐
│ 3. 대화 상태 병합 (agent.py)                         │
│    멀티턴 누적 슬롯 관리, 4회 clarify → escalate     │
└──────────────┬──────────────────────────────────────┘
               ▼
┌─────────────────────────────────────────────────────┐
│ 4. 저장소 조회 (storage.py → bookings.json)          │
│    전화번호 우선 식별, 초진/재진 판정                  │
└──────────────┬──────────────────────────────────────┘
               ▼
┌─────────────────────────────────────────────────────┐
│ 5. 정책 검사 (policy.py) — LLM 위임 금지             │
│    운영시간, 정원, 슬롯 겹침, 24시간 룰              │
└──────────────┬──────────────────────────────────────┘
               ▼
┌─────────────────────────────────────────────────────┐
│ 6. Cal.com 연동 (calcom_client.py) — Q4 선택         │
│    슬롯 교차검증 + 예약 생성 + 실패 시 저장 차단      │
└──────────────┬──────────────────────────────────────┘
               ▼
┌─────────────────────────────────────────────────────┐
│ 7. 영속화 + 응답 (storage.py + response_builder.py)  │
│    원자적 저장 + confidence/reasoning 동적 계산       │
└─────────────────────────────────────────────────────┘
```

```
chat.py ──┐
           ├──▶ src/agent.py (공유 핵심 로직)
run.py  ──┘         │
                     ├──▶ classifier.py ──▶ llm_client.py ──▶ Ollama
                     ├──▶ policy.py (순수 산술)
                     ├──▶ storage.py ──▶ bookings.json
                     ├──▶ calcom_client.py ──▶ Cal.com API
                     ├──▶ response_builder.py
                     └──▶ metrics.py
```

---

## 5. 설치 및 실행

### 사전 요구 사항

| 항목 | 버전 | 필수 |
|------|------|------|
| Python | 3.12+ | 필수 |
| Ollama | 0.4.0+ | 필수 |
| Cal.com 계정 | - | 선택 (Q4) |

### 설치

```bash
git clone <repo> && cd kobimedi-poc

# 가상환경 + 의존성
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# LLM 모델 (약 18GB, 최초 1회)
ollama pull qwen3-coder:30b

# Cal.com 연동 시 (선택)
cat > .env << 'EOF'
CALCOM_API_KEY=cal_live_xxx
CALCOM_ENT_ID=123       # 이비인후과
CALCOM_INTERNAL_ID=456   # 내과
CALCOM_ORTHO_ID=789      # 정형외과
EOF

# 설치 확인
./scripts/init.sh
```

### 실행

```bash
# 모드 1: 인터랙티브 챗봇
python chat.py

# 모드 2: 배치 처리
python run.py --input data/tickets.json --output results.json
```

### 문제 해결

| 증상 | 해결 |
|------|------|
| `ModuleNotFoundError` | `pip install -r requirements.txt` |
| `ollama._types.ResponseError` | `ollama serve` 실행 |
| `model not found` | `ollama pull qwen3-coder:30b` |
| Cal.com clarify 응답 | `.env` 확인 또는 무시 (로컬만으로 동작) |

---

## 6. 테스트

### 테스트 체계

| 레벨 | 수량 | 실행 | 속도 |
|------|------|------|------|
| **유닛 테스트** | 226개 | `pytest tests/` | ~9초 |
| **시나리오 테스트** | 51개 | `python scripts/run_scenario_tests.py` | ~80초 |

유닛 테스트는 Mock 기반으로 각 컴포넌트를 격리 검증한다. 시나리오 테스트는 실제 Ollama + Cal.com을 호출하여 대화 흐름 전체를 검증한다.

### 유닛 테스트 파일

| 파일 | 수량 | 대상 |
|------|------|------|
| `test_scenarios.py` | 51 | 9개 카테고리 시나리오 |
| `test_calcom.py` | 51 | Cal.com API 연동 |
| `test_safety.py` | 35 | Safety gate |
| `test_response_builder.py` | 27 | 응답 생성 |
| `test_classifier.py` | 20 | 의도 분류 |
| `test_policy.py` | 14 | 정책 엔진 |
| `test_dialogue.py` | 13 | 멀티턴 대화 |
| `test_storage.py` | 11 | 저장소 |
| `test_generalization.py` | 3 | 일반화 |
| `test_batch.py` | 1 | 배치 출력 |

### 시나리오 9개 카테고리

| # | 카테고리 | 수량 | LLM |
|---|---------|------|-----|
| 1 | 정상 예약 완료 | 4 | O |
| 2 | 환자 식별 & 대리 | 4 | O |
| 3 | 정책 엔진 슬롯 계산 | 5 | X |
| 4 | 24시간 변경/취소 | 5 | X |
| 5 | Safety Gate | 7 | O |
| 6 | 분과/운영시간 | 3 | O |
| 7 | 운영시간 정책 (F-052) | 12 | X |
| 8 | 대화 상태 관리 | 3 | O |
| 9 | Cal.com 외부 연동 | 8 | O |

상세 명세: [docs/test_scenarios.md](docs/test_scenarios.md)

### 실행 스크립트

```bash
./scripts/run_tests.sh              # 유닛만
./scripts/run_tests.sh --scenario   # 시나리오만
./scripts/run_tests.sh --all        # 전체
```

---

## 7. 스크립트

| 스크립트 | 용도 | 사용법 |
|---------|------|-------|
| `scripts/init.sh` | 환경 초기화 (venv + 의존성 + Ollama 확인) | `./scripts/init.sh` |
| `scripts/check.sh` | 전체 검증 (구문 + 테스트 + 배치 + Gold eval) | `./scripts/check.sh` |
| `scripts/run_tests.sh` | 유닛/시나리오 테스트 실행 + 결과 파일 생성 | `./scripts/run_tests.sh --all` |
| `scripts/run_scenario_tests.py` | 시나리오 러너 (카테고리별, 정책만 등) | `python scripts/run_scenario_tests.py --category 5` |
| `scripts/cleanup_bookings.py` | Cal.com 예약 일괄 삭제 + 로컬 동기화 | `python scripts/cleanup_bookings.py --dry-run` |

---

## 8. 프로젝트 구조

```
kobimedi-poc/
├── chat.py                      # 모드 1: 인터랙티브 챗봇
├── run.py                       # 모드 2: 배치 처리
├── src/
│   ├── agent.py                 # 핵심 파이프라인 (두 모드 공유)
│   ├── classifier.py            # Safety gate + 의도 분류
│   ├── policy.py                # 결정론적 정책 엔진
│   ├── storage.py               # bookings.json 저장소
│   ├── calcom_client.py         # Cal.com API v2
│   ├── response_builder.py      # 응답 생성
│   ├── llm_client.py            # Ollama 래퍼
│   ├── models.py                # 데이터 모델
│   └── metrics.py               # KPI 기록
├── scripts/                     # 운영 스크립트
├── tests/                       # 유닛 테스트 (226개)
├── data/
│   ├── tickets.json             # 입력 티켓 50건
│   └── bookings.json            # 예약 저장소 (진실원천)
└── docs/
    ├── final_report.md          # 최종 리포트 (Q1~Q4)
    ├── q1_metric_rubric.md      # PoC 성공 지표
    ├── q3_safety.md             # 안전성 대응 방안
    ├── architecture.md          # 아키텍처 설계
    ├── policy_digest.md         # 예약 정책 요약
    ├── demo_evidence.md         # 데모 증빙
    ├── test_scenarios.md        # 시나리오 명세 (51개)
    ├── test_results_unit.txt    # 유닛 테스트 결과
    └── test_results_scenario.txt # 시나리오 테스트 결과
```

---

## 9. 도메인 정보

### 진료 분과

| 분과 | 담당 의사 | cal.com Event Type | 슬롯 |
|------|----------|-------------------|------|
| 이비인후과 | 이춘영 원장 | ent-consultation | 30분 |
| 내과 | 김만수 원장 | internal-medicine | 30분 |
| 정형외과 | 원징수 원장 | orthopedics | 30분 |

### 진료시간

| 요일 | 시간 |
|------|------|
| 월~금 | 09:00-18:00 |
| 토요일 | 09:00-13:00 |
| 일요일/공휴일 | 휴진 |
| 점심시간 | 12:30-13:30 (예약 불가) |

### 증상 → 분과 안내

| 증상 키워드 | 안내 분과 |
|------------|----------|
| 코막힘, 귀 통증, 인후통, 편도선, 비염, 축농증, 중이염 | 이비인후과 |
| 소화불량, 복통, 혈압, 당뇨, 감기, 발열, 두통, 어지러움 | 내과 |
| 관절통, 허리 통증, 골절, 근육통, 무릎, 어깨, 목 통증 | 정형외과 |

> 이 매핑은 **안내 목적**이며 **진단이 아니다**.

### 에스컬레이션 기준

| 상황 | 근거 |
|------|------|
| 의료 관련 질문 | 안전 정책 4.1 |
| 급성 통증/응급 | 변경/취소 정책 3.3 |
| 감정적/화난 고객 (2회 이상 불만) | 고객 만족 |
| 정책으로 해결 불가한 복잡 케이스 | 판단 한계 |
| 보험/비용 구체적 문의 | 정보 한계 |
| 의사 개인정보/연락처 요청 | 안전 정책 4.4 |

---

## 10. AI 도구 활용

| 도구 | 용도 |
|------|------|
| Claude Code (Anthropic CLI) | 아키텍처 설계, 구현, 테스트 작성, 코드 리뷰, 문서 생성 |
| Ollama + qwen3-coder:30b | 챗봇 LLM (Safety gate 폴백, 의도 분류, 정보 추출) |
| Cal.com API v2 | 외부 예약 시스템 연동 (Q4) |

AI 코딩 에이전트 활용 harness: `.ai/handoff/` 디렉토리 참조.
