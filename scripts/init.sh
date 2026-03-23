#!/usr/bin/env bash
set -euo pipefail

echo "=== 환경 초기화 ==="
[ ! -d ".venv" ] && python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt

echo "[Ollama]"
if command -v ollama &>/dev/null; then
    ollama list 2>/dev/null | grep -q "qwen3-coder" && echo "  모델 OK" || echo "  경고: ollama pull qwen3-coder:30b 필요"
else
    echo "  경고: Ollama 미설치"
fi

echo "[Features]"
python3 -c "
import json; data=json.loads(open('.ai/harness/features.json').read())
print(f'  {sum(1 for f in data if f[\"passes\"])}/{len(data)} passed')
"

echo "[Next]"
head -15 .ai/harness/progress.md 2>/dev/null
echo "=== 완료 ==="