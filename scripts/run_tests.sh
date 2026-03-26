#!/usr/bin/env bash
# ---------------------------------------------------------------
# scripts/run_tests.sh
#
# 사용법:
#   ./scripts/run_tests.sh              # 유닛 테스트만 (기본)
#   ./scripts/run_tests.sh --scenario   # 시나리오 테스트만 (10개 카테고리)
#   ./scripts/run_tests.sh --all        # 유닛 + 시나리오 전체
#
# 결과 파일:
#   docs/test_results_unit.txt
#   docs/test_results_scenario.txt
# ---------------------------------------------------------------
set -euo pipefail

cd "$(dirname "$0")/.."
source .venv/bin/activate 2>/dev/null || true

RESULTS_DIR="docs"
UNIT_RESULT="$RESULTS_DIR/test_results_unit.txt"
SCENARIO_RESULT="$RESULTS_DIR/test_results_scenario.txt"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

mkdir -p "$RESULTS_DIR"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

RUN_UNIT=false
RUN_SCENARIO=false

case "${1:-}" in
    --scenario) RUN_SCENARIO=true ;;
    --all)      RUN_UNIT=true; RUN_SCENARIO=true ;;
    *)          RUN_UNIT=true ;;
esac

TOTAL_PASS=0
TOTAL_FAIL=0

# ---------------------------------------------------------------
# 유닛 테스트
# ---------------------------------------------------------------
run_unit_tests() {
    echo ""
    echo "== Unit Tests =="

    { echo "# Unit test results"; echo "# $TIMESTAMP"; echo ""; } > "$UNIT_RESULT"

    if python -m pytest tests/ -v --tb=short 2>&1 | tee -a "$UNIT_RESULT"; then
        echo -e "\n${GREEN}[PASS]${NC} Unit tests passed"
        UNIT_EXIT=0
    else
        echo -e "\n${RED}[FAIL]${NC} Unit tests failed"
        UNIT_EXIT=1
    fi

    UNIT_SUMMARY=$(tail -1 "$UNIT_RESULT")
    echo "  Result: $UNIT_SUMMARY"
    echo "  Saved:  $UNIT_RESULT"

    PASSED=$(echo "$UNIT_SUMMARY" | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' || echo "0")
    FAILED=$(echo "$UNIT_SUMMARY" | grep -oE '[0-9]+ failed' | grep -oE '[0-9]+' || echo "0")
    TOTAL_PASS=$((TOTAL_PASS + PASSED))
    TOTAL_FAIL=$((TOTAL_FAIL + FAILED))
    return $UNIT_EXIT
}

# ---------------------------------------------------------------
# 시나리오 테스트 (10개 카테고리, 실제 LLM + Cal.com)
# ---------------------------------------------------------------
run_scenario_tests() {
    echo ""
    echo "== Scenario Tests (10 categories) =="

    if python scripts/run_scenario_tests.py --output "$SCENARIO_RESULT" 2>&1; then
        echo -e "\n${GREEN}[PASS]${NC} Scenario tests completed"
        SCENARIO_EXIT=0
    else
        echo -e "\n${RED}[FAIL]${NC} Scenario tests had failures"
        SCENARIO_EXIT=1
    fi

    echo "  Saved: $SCENARIO_RESULT"

    if [ -f "$SCENARIO_RESULT" ]; then
        SC_LINE=$(grep "PASS:" "$SCENARIO_RESULT" | tail -1 || true)
        SC_PASS=$(echo "$SC_LINE" | grep -oE 'PASS: [0-9]+' | grep -oE '[0-9]+' || echo "0")
        SC_FAIL=$(echo "$SC_LINE" | grep -oE 'FAIL: [0-9]+' | grep -oE '[0-9]+' || echo "0")
        TOTAL_PASS=$((TOTAL_PASS + SC_PASS))
        TOTAL_FAIL=$((TOTAL_FAIL + SC_FAIL))
    fi

    return $SCENARIO_EXIT
}

# ---------------------------------------------------------------
# Main
# ---------------------------------------------------------------
echo "========================================================"
echo "  Kobimedi Test Runner - $TIMESTAMP"
echo "========================================================"

EXIT_CODE=0

[ "$RUN_UNIT" = true ]     && { run_unit_tests     || EXIT_CODE=1; }
[ "$RUN_SCENARIO" = true ] && { run_scenario_tests  || EXIT_CODE=1; }

echo ""
echo "== Summary =="
echo "  Passed: $TOTAL_PASS"
echo "  Failed: $TOTAL_FAIL"
echo ""
[ "$RUN_UNIT" = true ]     && echo "  Unit:     $UNIT_RESULT"
[ "$RUN_SCENARIO" = true ] && echo "  Scenario: $SCENARIO_RESULT"
echo ""

if [ $EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}=== ALL PASSED ===${NC}"
else
    echo -e "${RED}=== SOME FAILED ===${NC}"
fi

exit $EXIT_CODE
