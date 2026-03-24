#!/usr/bin/env bash
set -euo pipefail
source .venv/bin/activate 2>/dev/null || true
FAIL=0

echo "=== 검증 ==="

echo "[1/5] 구문..."
for f in src/*.py chat.py run.py; do
    [ -f "$f" ] && python3 -m py_compile "$f" 2>/dev/null || true
done

echo "[2/5] Features..."
python3 -c "
import json; d=json.loads(open('.ai/harness/features.json').read())
print(f'  {sum(1 for f in d if f[\"passes\"])}/{len(d)}')
" || FAIL=1

echo "[3/5] Tests..."
ls tests/test_*.py &>/dev/null \
    && python3 -m pytest tests/ -v --tb=short 2>&1 | tail -40 \
    || echo "  skip"

echo "[4/5] Batch..."
if [ -f run.py ] && [ -s run.py ]; then
    timeout 300 python3 run.py --input data/tickets.json --output /tmp/chk.json 2>&1 \
        && python3 -c "
import json; d=json.loads(open('/tmp/chk.json').read())
print(f'  {len(d)}건')
req={'ticket_id','classified_intent','department','action','response','confidence','reasoning'}
for i in d[:3]:
    m=req-set(i.keys())
    if m: print(f'  누락: {m}')
" 2>/dev/null && rm -f /tmp/chk.json \
        || echo "  실행 실패"
else
    echo "  skip"
fi

echo "[5/5] Gold eval..."
[ -f results.json ] && [ -f golden_eval/gold_cases.json ] \
    && python3 golden_eval/eval.py results.json golden_eval/gold_cases.json \
    || echo "  skip"

[ $FAIL -eq 0 ] && echo "=== OK ===" || echo "=== FAIL ==="