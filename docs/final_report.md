# Kobimedi AI Agent PoC Report

## Table of Contents
- [1. Executive Summary](#1-executive-summary)
- [2. Q1. Metric Rubric](#2-q1-metric-rubric)
- [3. Q2. Agent Architecture & Design Decisions](#3-q2-agent-architecture--design-decisions)
- [4. Q2. Interactive Demo Evidence](#4-q2-interactive-demo-evidence)
- [5. Q3. Safety Response Plan](#5-q3-safety-response-plan)
- [6. AI Tools and Harness Disclosure](#6-ai-tools-and-harness-disclosure)
- [7. Q4. cal.com Integration Summary](#7-q4-calcom-integration-summary)

## 1. Executive Summary
본 문서는 가상의 중형 네트워크 병원 '코비메디'의 진료 예약 접수 및 변경/취소를 자동화하는 AI Agent PoC의 최종 결과 보고서입니다. 본 시스템은 단순한 LLM의 대화 능력에 기대지 않고, **의료 상담의 원천 차단**, **결정론적 예약 정책 준수**, **환자 본인/대리인의 명확한 식별**을 최우선 핵심 가치로 삼아 아키텍처 레벨에서 설계되었습니다. 

## 2. Q1. Metric Rubric
요구사항의 비용 구조(성공 +$10, 소프트실패 -$20, 하드실패 -$500) 분석 결과, **하드 실패 1건이 성공 50건의 이득을 모두 상쇄**하는 것으로 나타났습니다. 이에 따라 허위 예약 확정이나 의료 정보 제공 오류를 0%로 통제하는 안전 지표를 KPI로 제안합니다.
*(상세 내용은 `docs/q1_metric_rubric.md` 참조)*

## 3. Q2. Agent Architecture & Design Decisions
### 아키텍처 파이프라인
본 Agent는 LLM에 의존한 단순 챗봇이 아니라 아래의 엄격한 7단계 파이프라인을 거칩니다.
1. **Safety Gate**: 의료 상담, 응급 상황, 프롬프트 인젝션을 최우선으로 필터링 (`reject` 또는 `escalate`).
2. **Intent & Extraction**: 안전한 요청에 한해 LLM(Ollama qwen3-coder:30b)을 이용해 의도와 엔티티 추출.
3. **Dialogue State Merge**: 멀티턴 대화 내의 누락 정보(이름, 연락처, 분과 등) 추적 관리.
4. **Storage Lookup**: 전화번호를 1차 식별자로 삼아 로컬 DB(`data/bookings.json`)에서 기존 예약 및 초진/재진 조회.
5. **Deterministic Policy Engine**: LLM 개입을 배제한 Python 코드 기반 정책 검사.
6. **Cal.com Integration (Q4)**: 외부 캘린더 슬롯 교차검증 + 예약 생성. API 미설정 시 건너뜀 (Graceful Degradation).
7. **Persist & Response**: 최종 확인 절차 후 예약 확정 및 응답.

### 주요 설계 결정 근거
**A. 본인/대리인 확인을 가장 먼저 수행하는 대화 설계**
- **배경**: 예약자의 이름(`customer_name`)만으로는 실제 진료받을 환자인지 알 수 없습니다.
- **결정**: 예약 의도가 파악되면 어떤 정보(날짜, 시간)보다 먼저 **"예약하시는 분이 본인이신가요, 대리인이신가요?"** 라고 묻도록 강제했습니다. 이를 통해 대리인 이름으로 예약이 확정되는 치명적 하드 실패(-$500)를 막고, 동명이인 식별이 가능한 '전화번호'를 올바른 환자 식별 진실원천(Source of Truth)으로 삼을 수 있었습니다.

**B. LLM 위임을 완전히 배제한 결정론적 정책 엔진 (`src/policy.py`) 도입**
- **배경**: "1시간당 최대 3명", "24시간 이전만 취소 가능", "초진 40분 확보" 같은 산술적이고 절대적인 규칙을 LLM 프롬프트에 맡기면 필연적으로 할루시네이션(예: 점심시간 무시, 정원 임의 초과 허용)이 발생합니다.
- **결정**: LLM은 오직 자연어 이해 및 정보 추출기로만 사용하고, 예약 허용 여부는 파이썬 코드 기반의 룰 엔진으로만 산술 계산하게 하여 정책 위반 케이스를 0%로 만들었습니다.

## 4. Q2. Interactive Demo Evidence
명세된 시나리오는 모두 실제 작동 코드 기반으로 구현되었으며 테스트(`pytest`)를 통해 동작을 입증합니다.
- **정상 예약 처리**: `chat.py`를 통해 본인 확인 → 환자 연락처 수집 → 분과 및 시간 확인 → 결정론 정책 검사를 거쳐 예약을 생성하는 흐름.
- **의료 상담 거부**: "이 약 먹어도 되나요?" 입력 시 파이프라인 1단계에서 즉각 차단되고, 고정된 안전 안내 문구로 우회하는 흐름.
- **모호한 요청 → Clarification**: "예약하고 싶어요"와 같이 정보가 부족한 입력에 대해, 누락된 정보를 큐(Queue) 형태로 관리하여 순차적으로 정보를 묻고 수집해내는 누적 상태 머신 동작.

## 5. Q3. Safety Response Plan
LLM의 환각 현상을 억제하기 위해, 예약 의도를 판단하기 전 파이프라인 최상단에 `classify_safety()` 로직을 배치하여 '생성 전 사전 차단'을 구현한 전략을 구체화했습니다. 
*(상세 내용은 `docs/q3_safety.md` 참조)*

## 6. AI Tools and Harness Disclosure
본 PoC 설계 및 개발 과정에서 생산성 극대화를 위해 AI 코딩 도구를 적극 활용했습니다.

- **사용한 AI 도구**:

| 도구 | 역할 | 활용 내용 |
|------|------|----------|
| ChatGPT 5.4 (OpenAI) | 계획 수립 | 과제 요구사항 분석, 아키텍처 설계 방향 결정, 작업 계획 수립, 기능 체크리스트 설계 |
| Claude Code (Anthropic, claude-sonnet-4-6) | 코드 작성 | 핵심 로직 구현(`src/` 전체), 테스트 작성(236개 유닛 + 61개 시나리오), 문서 생성 |
| Gemini 2.5 Pro (Google) | 코드 리뷰 | 정책 준수 여부 검토, AGENTS.md Non-Negotiables 대비 검증, 엣지 케이스 발굴 |
| Cline (VS Code Extension) | 에이전트 오케스트레이션 | 계획→구현→리뷰→수정 사이클을 IDE 안에서 관리 |

- **활용 패턴 (Planner → Implementer → Reviewer)**:
  - **Plan** (ChatGPT 5.4): 과제 요구사항을 분석하여 `.ai/handoff/10_plan.md` 및 기능 명세 `features.json`의 체크리스트를 설계하고 우선순위를 결정.
  - **Implement** (Claude Code): 도출된 TDD 기반 체크리스트를 따라 `src/` 핵심 로직 구현, 테스트 코드 작성, 문서 생성.
  - **Review** (Gemini 2.5 Pro): `CLAUDE.md`의 10가지 Review Priorities 기준으로 구현 검토, 의료 상담 우회 가능성·정책 결정론 위반·거짓 성공 등 검증.
  - **Orchestration** (Cline): `.ai/handoff/` 문서를 통해 각 에이전트에게 컨텍스트를 전달하고 단계 간 인수인계를 관리.

- **프롬프트 원문**: [docs/prompts.md](prompts.md)에 Phase 1~7 프롬프트 전문을 수록.

## 7. Q4. cal.com Integration Summary
*(선택 과제: 로컬 DB `data/bookings.json`을 진실원천으로 삼는 아키텍처 흐름 안에서, Policy Check 통과 이후 확정 단계 직전에 외부 슬롯 확인 및 API 호출이 자연스럽게 결합될 수 있는 연동 확장 구조로 설계되었습니다.)*
