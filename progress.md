# Project Progress

## Current status
- **Phase 2 Complete**: Safety-First 게이트웨이 및 예외 방어 구현 완료.
  - `src/classifier.py`의 `safety_check()` 로직 구현 및 검증 완료.
  - `src/agent.py`의 `process_ticket`에서 파이프라인 최선행으로 `safety_check()` 실행 확인.
  - 의료 상담(진단/약물), 잡담, 프롬프트 인젝션, 타 환자 정보 요청 즉시 reject 처리.
  - 응급/급성 통증, 화난 고객/상담원 요청, 보험·비용·의사 개인정보 문의 즉시 escalate 처리.
  - 혼합 요청(의료+예약) 분리 로직: 예약 하위 문자열만 추출하여 후단 전달, 분리 불가 시 전체 reject.
  - 증상 기반 분과 '안내'만 제공하고 진단 텍스트 생성 금지 처리.
  - 관련된 모든 기능 요구사항(F-001~F-010)을 충족하고 `features.json`에 반영함.
  - `tests/test_safety.py` 35개 단위 테스트 전부 통과 확인.

- **Phase 1 Complete**: `src/storage.py` 구현 및 단위 테스트 완료.
  - `data/bookings.json`을 진실원천으로 사용하는 예약 저장소 모듈 구현.
  - 전화번호 우선 환자 식별, 필수 필드 검증, 예외 처리, 동시성 방어 기능 포함.
  - 관련된 모든 기능 요구사항(F-034~F-039, F-061~F-067)을 충족하고 `features.json`에 반영함.
  - `pytest`를 통해 모든 단위 테스트 통과를 확인함.

## Next step
- **Proceed to Phase 3**: Cal.com 연동 및 통합 테스트.
  - `src/calcom_client.py`를 통한 cal.com API 연동 로직 구현 및 검증.
  - 전체 파이프라인 통합 테스트(golden_eval) 실행 및 결과 검토.
