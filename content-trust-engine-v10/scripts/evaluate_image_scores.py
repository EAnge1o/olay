"""Evaluate already collected detector signals; this script makes no API calls."""

import argparse
import json
from pathlib import Path

from content_trust.evaluation.image_metrics import evaluate_image_scores


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jsonl", type=Path, help="Labeled image score JSONL")
    parser.add_argument("--target", required=True, help="Target label name")
    parser.add_argument("--target-fpr", type=float, default=0.05)
    parser.add_argument("--ece-bins", type=int, default=10)
    args = parser.parse_args()
    records = [
        json.loads(line)
        for line in args.jsonl.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    result = evaluate_image_scores(
        records,
        target=args.target,
        target_fpr=args.target_fpr,
        ece_bins=args.ece_bins,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
