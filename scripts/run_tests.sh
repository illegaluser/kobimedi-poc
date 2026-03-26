#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# scripts/run_tests.sh — 시나리오 테스트 + E2E 테스트 실행 스크립트
#
# 사용법:
#   ./scripts/run_tests.sh              # 유닛 테스트만 (기본)
#   ./scripts/run_tests.sh --e2e        # E2E 테스트만
#   ./scripts/run_tests.sh --all        # 유닛 + E2E 전체
#
# 결과 파일:
#   docs/test_results_unit.txt          # 유닛 테스트 결과
#   docs/test_results_e2e.txt           # E2E 테스트 결과
# ──────────────────────────────────────────────────────────────
set -euo pipefail

cd "$(dirname "$0")/.."
source .venv/bin/activate 2>/dev/null || true

RESULTS_DIR="docs"
UNIT_RESULT="$RESULTS_DIR/test_results_unit.txt"
E2E_RESULT="$RESULTS_DIR/test_results_e2e.txt"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

mkdir -p "$RESULTS_DIR"

# ── 색상 ──
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# ── 인자 파싱 ──
RUN_UNIT=false
RUN_E2E=false

case "${1:-}" in
    --e2e)   RUN_E2E=true ;;
    --all)   RUN_UNIT=true; RUN_E2E=true ;;
    *)       RUN_UNIT=true ;;
esac

TOTAL_PASS=0
TOTAL_FAIL=0

# ──────────────────────────────────────────────────────────────
# 유닛 테스트 (9개 카테고리 시나리오 + 기존 전체)
# ──────────────────────────────────────────────────────────────
run_unit_tests() {
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo " 유닛 테스트 (시나리오 51개 + 기존 163개)"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    {
        echo "# 유닛 테스트 결과"
        echo "# 실행 시각: $TIMESTAMP"
        echo "# 실행 명령: pytest tests/ -v --tb=short"
        echo ""
    } > "$UNIT_RESULT"

    # pytest 실�� (E2E ��동 제외 — pytest.ini addopts)
    if python -m pytest tests/ -v --tb=short 2>&1 | tee -a "$UNIT_RESULT"; then
        echo ""
        echo -e "${GREEN}[PASS]${NC} 유닛 테스트 전체 통과"
        UNIT_EXIT=0
    else
        echo ""
        echo -e "${RED}[FAIL]${NC} 유닛 테스트 실패 발생"
        UNIT_EXIT=1
    fi

    # 결과 요약 추출
    UNIT_SUMMARY=$(tail -1 "$UNIT_RESULT")
    echo ""
    echo "  결과: $UNIT_SUMMARY"
    echo "  저장: $UNIT_RESULT"

    # 통과/실패 수 집계
    PASSED=$(echo "$UNIT_SUMMARY" | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' || echo "0")
    FAILED=$(echo "$UNIT_SUMMARY" | grep -oE '[0-9]+ failed' | grep -oE '[0-9]+' || echo "0")
    TOTAL_PASS=$((TOTAL_PASS + PASSED))
    TOTAL_FAIL=$((TOTAL_FAIL + FAILED))

    return $UNIT_EXIT
}

# ──────────────────────────────────────────────────────────────
# E2E 테스트 (Ollama + Cal.com 실제 호출)
# ──────────────────────────────────────────────────────────────
run_e2e_tests() {
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo " E2E 테스트 (28개, Ollama + Cal.com 실제 호출)"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    # 환경 체크
    echo ""
    echo "  환경 점검:"

    # Ollama 체크
    if command -v ollama &>/dev/null && ollama list 2>/dev/null | grep -q "qwen3-coder"; then
        echo -e "    Ollama:  ${GREEN}OK${NC} (qwen3-coder:30b 로드됨)"
        OLLAMA_OK=true
    else
        echo -e "    Ollama:  ${YELLOW}SKIP${NC} (미구동 또는 모델 미로드)"
        OLLAMA_OK=false
    fi

    # Cal.com 체크
    if [ -f .env ]; then
        # .env 로드 (기존 환경변수 오염 방지를 위해 서브쉘에서만 사용)
        CALCOM_KEY=$(grep -E '^CALCOM_API_KEY=' .env | cut -d= -f2- || true)
        if [ -n "$CALCOM_KEY" ]; then
            echo -e "    Cal.com: ${GREEN}OK${NC} (API 키 설정됨)"
            CALCOM_OK=true
        else
            echo -e "    Cal.com: ${YELLOW}SKIP${NC} (API 키 미설정)"
            CALCOM_OK=false
        fi
    else
        echo -e "    Cal.com: ${YELLOW}SKIP${NC} (.env 파일 없음)"
        CALCOM_OK=false
    fi

    if [ "$OLLAMA_OK" = false ] && [ "$CALCOM_OK" = false ]; then
        echo ""
        echo -e "  ${YELLOW}[SKIP]${NC} E2E 환경이 준비되지 않아 건너뜁니다."
        echo "# E2E 테스트 결과 — SKIPPED (환경 미충족)" > "$E2E_RESULT"
        echo "# 실행 시각: $TIMESTAMP" >> "$E2E_RESULT"
        return 0
    fi

    {
        echo "# E2E 테스트 결과"
        echo "# 실행 시각: $TIMESTAMP"
        echo "# Ollama: $OLLAMA_OK | Cal.com: $CALCOM_OK"
        echo "# 실행 명령: pytest tests/test_e2e.py -v -m e2e --tb=short"
        echo ""
    } > "$E2E_RESULT"

    echo ""

    # .env에서 환경변수 로드 후 E2E 실행
    if (
        set -a
        [ -f .env ] && source .env
        set +a
        python -m pytest tests/test_e2e.py -v -m e2e -o "addopts=" --tb=short 2>&1
    ) | tee -a "$E2E_RESULT"; then
        echo ""
        echo -e "${GREEN}[PASS]${NC} E2E 테스트 전체 통과"
        E2E_EXIT=0
    else
        echo ""
        echo -e "${RED}[FAIL]${NC} E2E 테스트 실패 발생"
        E2E_EXIT=1
    fi

    # 결과 요약 추출
    E2E_SUMMARY=$(tail -1 "$E2E_RESULT")
    echo ""
    echo "  결과: $E2E_SUMMARY"
    echo "  저장: $E2E_RESULT"

    PASSED=$(echo "$E2E_SUMMARY" | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' || echo "0")
    FAILED=$(echo "$E2E_SUMMARY" | grep -oE '[0-9]+ failed' | grep -oE '[0-9]+' || echo "0")
    TOTAL_PASS=$((TOTAL_PASS + PASSED))
    TOTAL_FAIL=$((TOTAL_FAIL + FAILED))

    return $E2E_EXIT
}

# ──────────────────────────────────────────────────────────────
# 실행
# ──────────────────────────────────────────────────────��───────
echo "╔══════════════════════════════════════════════════════╗"
echo "║   코비메디 예약 챗봇 — 테스트 실행기                ║"
echo "║   실행 시각: $TIMESTAMP              ║"
echo "╚══════════════════════════════════════════════════════╝"

EXIT_CODE=0

if [ "$RUN_UNIT" = true ]; then
    run_unit_tests || EXIT_CODE=1
fi

if [ "$RUN_E2E" = true ]; then
    run_e2e_tests || EXIT_CODE=1
fi

# ── 최종 요약 ──
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " 최종 요약"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  총 통과: $TOTAL_PASS"
echo "  총 실패: $TOTAL_FAIL"
echo ""

if [ "$RUN_UNIT" = true ]; then
    echo "  유닛 결과: $UNIT_RESULT"
fi
if [ "$RUN_E2E" = true ]; then
    echo "  E2E 결과:  $E2E_RESULT"
fi

echo ""
if [ $EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}=== ALL PASSED ===${NC}"
else
    echo -e "${RED}=== SOME FAILED ===${NC}"
fi

exit $EXIT_CODE
