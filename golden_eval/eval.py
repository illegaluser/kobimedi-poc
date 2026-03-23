"""배치 결과를 골드 라벨과 비교"""
import json, sys

def evaluate(results_path, labels_path):
    results = {r["ticket_id"]: r for r in json.loads(open(results_path).read())}
    labels = json.loads(open(labels_path).read())
    total, ok, rej_ok, rej_tot = len(labels), 0, 0, 0
    for l in labels:
        tid = l["ticket_id"]
        if tid not in results: print(f"  누락: {tid}"); continue
        r = results[tid]
        if r.get("action") == l["expected_action"]: ok += 1
        else: print(f"  오분류: {tid} 예상={l['expected_action']} 실제={r.get('action')}")
        if l.get("expected_reject"):
            rej_tot += 1
            if r.get("action") == "reject": rej_ok += 1
            else: print(f"  reject 미탐: {tid}")
    print(f"\nAction 정확도: {ok}/{total} ({ok/total*100:.1f}%)")
    if rej_tot: print(f"Reject 재현율: {rej_ok}/{rej_tot} ({rej_ok/rej_tot*100:.1f}%)")

if __name__ == "__main__":
    if len(sys.argv) != 3: print("사용법: python golden_eval/eval.py results.json gold_cases.json"); sys.exit(1)
    evaluate(sys.argv[1], sys.argv[2])