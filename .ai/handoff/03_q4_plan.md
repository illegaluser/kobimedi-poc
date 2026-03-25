# Q4: cal.com 연동 실행 계획 (Plan)

## 단계 1: 환경 변수 및 설정 준비
- [ ] `.env` 파일 또는 환경 변수를 통해 `CALCOM_API_KEY` 로드 로직 구현
- [ ] 3개 분과에 대한 `Event Type ID` 매핑을 위한 환경 변수(`CALCOM_ENT_ID`, `CALCOM_INTERNAL_ID`, `CALCOM_ORTHO_ID`) 처리 준비

## 단계 2: `calcom_client.py` 구현
- [ ] `is_calcom_enabled()`: API Key 존재 여부에 따른 활성화 체크 함수 작성
- [ ] `get_available_slots(department, target_date)`:
  - `GET https://api.cal.com/v2/slots` 호출
  - Bearer 토큰 및 `cal-api-version: 2024-09-04` 헤더 추가
  - v2 API의 중첩 JSON 응답 구조 파싱 및 가용 `HH:MM` 리스트 반환
  - 실패 시 `None` 반환하여 에러 구분
- [ ] `create_booking(department, date, time, patient_name, patient_contact)`:
  - `POST https://api.cal.com/v2/bookings` 호출
  - Bearer 토큰 및 `cal-api-version: 2024-08-13` 헤더 추가
  - `attendee` 객체 및 `start` 시간(ISO 8601) 구성
  - 실패 시 `None` 반환

## 단계 3: `agent.py` 파이프라인 통합
- [ ] 예약 확인 질문(Confirmation) 생성 전, `calcom_client.get_available_slots`을 호출하여 선택한 시간이 가용한지 재검증. 실패/마감 시 `clarify`로 폴백.
- [ ] `pending_confirmation` 승인(네, 진행해주세요 등) 시, 로컬 `create_booking()` 호출 **직전**에 `calcom_client.create_booking()` 시도.
- [ ] 배치 모드(`!is_chat`)의 확정 단계에서도 동일하게 Booking 생성 로직 추가.
- [ ] Cal.com 연동 실패 시 `AGENT_HARD_FAIL` 지표 기록.

## 단계 4: 대응해야 할 유저 동선 (User Journeys) 상세 정의
에이전트가 완벽하게 커버하고 방어해야 할 cal.com 연동 시나리오의 상세 목록입니다.

### 1. 정상 예약 완료 흐름 (Happy Paths)
- [ ] **동선 1.1 (단번에 예약 성공)**: 로컬 정책 검증 통과 → cal.com 가용 슬롯에 요청 시간 존재 → 사용자 최종 동의("네") → cal.com 예약 생성 성공 → 로컬 영속화 → 성공 안내.
- [ ] **동선 1.2 (대안 슬롯 수락 후 성공)**: 사용자가 요청한 시간이 cal.com에서 이미 마감됨 → 에이전트가 대안 슬롯(예: 1번 10:00, 2번 10:30) 제시 → 사용자가 "1번이요" 선택 → 변경된 슬롯으로 가용성 재확인 및 동의 → 예약 생성 성공.
- [ ] **동선 1.3 (가용 시간 선제적 공유)**: 사용자가 분과와 날짜는 제시했으나 시간은 누락함 → cal.com API로 당일 가용 슬롯을 조회하여 "가능한 시간은 [가용 리스트] 입니다"라며 안내 (`clarify`).

### 2. 외부 캘린더 슬롯 선점 및 충돌 (Conflict / Soft Fails)
- [ ] **동선 2.1 (대안 거절 및 재탐색)**: cal.com 슬롯 마감으로 대안을 제시했으나, 사용자가 대안을 거절하고 "그럼 다음 주 월요일은요?"라고 아예 다른 날짜를 요구 → 새로운 날짜로 로컬 정책 및 cal.com 가용성을 처음부터 다시 조회 (`clarify` 루프 정상 동작).
- [ ] **동선 2.2 (Race Condition 방어)**: 확인 질문 시점(`get_available_slots`)에는 자리가 있었으나, 사용자가 "네"라고 대답하는 사이 외부에서 예약이 차버려 `create_booking` 호출 시 충돌(Conflict) 에러 반환 → 로컬 DB 저장 중단, "방금 전 외부 캘린더에서 예약이 마감되었습니다. 다른 시간을 선택해주세요."라고 안내.

### 3. 시스템 및 네트워크 장애 방어 (Hard Fail Defense)
- [ ] **동선 3.1 (가용 시간 조회 장애)**: `get_available_slots` 호출 시 타임아웃 또는 500 에러 발생 → "현재 외부 예약 시스템 응답 지연으로 가용 시간을 확인할 수 없습니다. 잠시 후 다시 시도해주세요." 안내 (`AGENT_HARD_FAIL` + 거짓 성공 원천 차단).
- [ ] **동선 3.2 (예약 생성 장애)**: 사용자 동의 후 `create_booking`을 호출했으나 타임아웃/오류 발생 → 로컬 DB 기록 중단, "예약 시스템 응답 지연으로 예약이 확정되지 않았습니다." 안내 (`AGENT_HARD_FAIL`).

### 4. 설정 누락 및 우회 (Graceful Degradation)
- [ ] **동선 4.1 (API Key 미설정)**: `.env`에 `CALCOM_API_KEY`가 없거나 빈 값임 → 에러를 뱉지 않고(`is_calcom_enabled() == False`), 로컬 정책과 로컬 저장소만으로 기존 에이전트와 동일하게 예약 처리 (외부 연동 없이도 시스템 정상 구동 보장).
- [ ] **동선 4.2 (매핑된 Event ID 없음)**: 분과(예: 치과)에 해당하는 `Event Type ID`가 매핑되어 있지 않음 → 연동 로직 우회 후 로컬 정책만으로 처리.

### 5. 배치 모드 (Batch Mode) 특화 동선
- [ ] **동선 5.1 (배치 처리 중 슬롯 선점)**: 배치 처리(`tickets.json`) 중 cal.com 가용 시간을 조회했는데 자리가 없음 → 사용자와 상호작용(대안 묻기)이 불가하므로 즉시 `clarify` 액션과 함께 대안 슬롯 텍스트를 응답 JSON에 담아 반환하고 로컬 확정 중단.
- [ ] **동선 5.2 (배치 처리 중 예약 즉시 생성)**: 배치 처리에서 로컬 정책과 cal.com 가용성을 모두 통과함 → 사용자 확인 턴을 생략하고 즉시 `calcom_client.create_booking`을 호출하여 예약 확정 후 결과를 반환.