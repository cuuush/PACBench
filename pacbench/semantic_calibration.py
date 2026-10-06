"""Run a frozen contract's semantic judge against proposed human labels."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .creds import load_driver
from .model_calls import ModelClient
from .semantic_contract import load_contract
from .semantic_grade import Claim, grade_semantic


def calibrate(contract_path: Path, examples_path: Path, output_dir: Path) -> dict:
    contract = load_contract(contract_path)
    examples = json.loads(examples_path.read_text())
    if examples["contract_hash"] != contract.contract_hash:
        raise ValueError("calibration labels refer to another contract")
    ids = [c.id for c in contract.claims]
    if examples["claim_ids"] != ids:
        raise ValueError("calibration claim IDs differ from contract")
    output_dir.mkdir(parents=True, exist_ok=True)
    driver = load_driver()
    judge = ModelClient(key=os.environ["META_API_KEY"], model=driver.model_id,
                        url=driver.base_url + "/chat/completions",
                        record_dir=output_dir / "model-calls",
                        cache_dir=Path(__file__).resolve().parents[1] / "runs" / "model-cache")
    claims = tuple(Claim(c.id, c.statement, c.evidence) for c in contract.claims)
    results = []
    for example in examples["examples"]:
        row = {"id": example["id"], "proposed": example["proposed"]}
        try:
            grade = grade_semantic("calibration/" + example["id"],
                                   contract.question, example["answer"], claims,
                                   judge, record_path=output_dir / (example["id"] + ".json"))
            row["judge"] = [s.verdict for s in grade.slots]
            row["agree"] = row["judge"] == row["proposed"]
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        results.append(row)
    report = {"status": "pending_human_review", "contract_hash": contract.contract_hash,
              "results": results}
    (output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("contract", type=Path)
    parser.add_argument("examples", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(calibrate(args.contract, args.examples, args.output), indent=2))


if __name__ == "__main__":
    main()
