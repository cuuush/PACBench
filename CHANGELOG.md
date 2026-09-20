# CHANGELOG

## 0.1.0 (2026-09-20)

Standalone benchmark package: corpus + validation + freeze/versioning +
deterministic grading + runner CLI over isolated Hermes turns.

- Corpus: three synthetic development lives (`dentist-depth`,
  `poker-collision`, `coat-weekend`, 7 simulated days each plus history) with
  matched controls, plus `poker-move` / `poker-unchanged` and
  `weekend-plans` / `dentist-friday` regression coverage. Truth, observations,
  and checkpoint rubrics kept distinct; support manifest never scored-run
  visible.
- Grading: atomic slot judgments (CORRECT / MISSED / MISREPRESENTED) with
  deterministic normalization; report aggregates task success, precision /
  recall / F1, current vs historical accuracy, plan accuracy, abstention,
  opportunity recall, forbidden suggestions, difficulty tags.
- Runner (W4): `python -m pacbench list-lives` (offline), `smoke`,
  `run-checkpoint`, `run-pilot` through the PACBench-owned isolated Hermes
  clone (`vendor/`, shims via `patches/`); scratch homes only, never the live
  profile. Memcal provider adapter behind the contract
  (`provider_adapters/memcal_adapter.py`). One checkpoint proven live end to
  end; no comparative results claimed.
- Generation: authoring spec + `must_include` render gate; Spark rendering
  optional via `.env` (see `.env.example`).

Not in 0.1.0: calibrated semantic judge for paraphrased TEXT slots
(unmatched paraphrases grade MISSED), model-based fact-realization
validation, 30-life comparative corpus, comparative provider results.
