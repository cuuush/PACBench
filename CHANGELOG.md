# CHANGELOG

## Unreleased (corpus 0.2.0)

- Corpus: new development lives `reunion-thread` (D) — thread-size difficulty
  (move + hearsay + stale repeat buried in a ~150-message group chat, work
  picnic as small-thread control) — and `revoke-week` (E) —
  cancellation-reversal (exactly one active booking), explicit forget (old
  code stays history, never current), completed-task silence. Corpus now 298
  observations / 33 checkpoints across 9 lives; freeze hash re-pinned
  deliberately (twice: new life, then calibration roles).
- Grading: polarity is atemporal — YESNO forbidden values match on the full
  answer (past-tense verbs no longer eat them via history-cue stripping);
  every required YESNO slot now names the opposite polarity as forbidden, so
  false affirmations/denials grade MISREPRESENTED, not MISSED (invariant
  tested). Same class fix for HISTORICAL slots: a wrong past value with the
  expected absent is confabulation even when past-tense phrasing hides it
  from the current-text filter — while history-aware answers naming both
  values stay CORRECT. Regrading pre-0.2.0 frozen artifacts under this
  grader shifts old MISSED verdicts — compare frozen grades only, never
  recompute. Prompt-leak validation is word-boundaried (substring matching
  overfired: "no" lives inside "Nova").
- Runner: `--provider lexical` (§27 diagnostic baseline, stdlib-only) for
  `run-checkpoint` / `run-pilot`; first measured comparison (lexical F1 0.750
  vs frozen memcal 0.727 on the 3 shared lives — a sanity check at 55 obs,
  not a finding; per the co-author the lexical baseline does NOT satisfy
  §45.5, which needs a real external memory system:
  `docs/comparative-gates.md` records all 14 gates for
  `comparative_claim_valid`, now split from `efficiency_claim_valid`).
- Usage: per-turn driver tokens (session-delta method) + memcal dream spend
  in pilot efficiency; artifact redaction preserves token-count keys.
- Splits & repetitions (§§39–40): declared split table (5 development, 4
  calibration, 0 held-out — the empty held-out is why no comparison is
  valid); `run-pilot --repetitions N` runs each life N times into namespaced
  dirs with per-rep F1 in the report. Pilot labels fixed (`dev-pilot`, live
  life/rep counts in REPORT.md instead of a stale "3-life" string).
- Readiness honesty (§42) + real-time allowance (§30): only a finished
  background pass reads ready — memcal skips and pre-run states read
  not-ready (a skip means the provider was not fully exercised), same for
  the lexical no-op before its first pass. `live.maintain` already treated
  both as incomplete; readiness now agrees instead of contradicting it. Run
  manifests record the declared per-turn real-time budget (`turn_timeout_s`).

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
