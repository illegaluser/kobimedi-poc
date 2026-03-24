"""배치 결과를 골드 라벨과 비교하여 정확도를 측정합니다.
사용법: python golden_eval/eval.py results.json golden_eval/gold_cases.json
"""
import json
import sys


def evaluate(results_path, labels_path):
    results = {r["ticket_id"]: r for r in json.loads(open(results_path).read())}
    labels = json.loads(open(labels_path).read())

    total = len(labels)
    correct_action = 0
    correct_reject = 0
    total_reject = 0

    for label in labels:
        tid = label["ticket_id"]
        if tid not in results:
            print(f"  누락: {tid}")
            continue

        result = results[tid]

        if result.get("action") == label["expected_action"]:
            correct_action += 1
        else:
            print(f"  오분류: {tid} — 예상 {label['expected_action']}, "
                  f"실제 {result.get('action')}")

        if label.get("expected_reject"):
            total_reject += 1
            if result.get("action") == "reject":
                correct_reject += 1
            else:
                print(f"  reject 미탐: {tid}")

    print(f"\n=== 평가 결과 ===")
    print(f"Action 정확도: {correct_action}/{total} "
          f"({correct_action / total * 100:.1f}%)")
    if total_reject > 0:
        print(f"Reject 재현율: {correct_reject}/{total_reject} "
              f"({correct_reject / total_reject * 100:.1f}%)")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("사용법: python golden_eval/eval.py results.json "
              "golden_eval/gold_cases.json")
        sys.exit(1)
    evaluate(sys.argv[1], sys.argv[2])