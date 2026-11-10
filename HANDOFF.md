# PACBench handoff

Updated November 10, 2026. Read this first, then [AGENTS.md](AGENTS.md) for the
operating rules. Maintain this single handoff rather than adding dated reports.

## Goal and repositories

Benchmark local memory systems after realistic nightly ingestion of personal
messages and emails. Hermes answers later using only the selected provider's
shipped tools. Compare retained state, answer quality, source fallback and
observed cost fairly; do not assume Memcal wins. Grow gradually toward roughly
60 authored scenarios covering different personal lives, often 30 days each.

- [PACBench](https://github.com/cuuush/PACBench), branch `work`: harness, specs,
  explorer and selected public evidence. `be93cab` introduced the current spec
  organization; this handoff follows it.
- [Gateway](https://github.com/cuuush/muse-gateway), branch `main`: independent
  repository, preserved history through `8d770a9`, README and installation metadata.
  The public repository was created by the user; `memcalbot` can now push.
- [Memcal](https://github.com/cuuush/memcal), branch `main`: product bugs belong
  here. The restored runtime was updated to `30a1fee` (0.8.7); published month
  results used an earlier development revision, not this newly restored runtime.

The `cloud-instance-backup-20261109` branch in Memcal preserves the divergent
older local history at `babc166`; do not merge it into current main casually.
The same-named PACBench branch archives the legacy standalone cloud installer.
PACBench's `gateway-recovery-20261019` branch is a historical gateway backup,
not the active gateway development branch.

## Critical blocker before paid work

The restored cloud instance has **no `/workspace/shared/muse-gateway` directory**.
Its original ledger, local client token and address are missing. Recovering source
from Git did not recover this state. No paid inference ran during the latest
restoration/spec audit/repository migration work.

Recover the real private gateway state before starting inference. Do not create a
blank replacement ledger or reconstruct one from incomplete public call receipts.
Keep it outside Git; a stopped gateway's whole state directory can be copied for
a consistent backup. Managed model credentials must also be configured separately.

All writer, verifier, judge, Hermes and provider inference must use Spark
Contributor through this one gateway. The cap is **$10 per UTC day**, not lifetime.
Daily budget refusals stop live work until reset. Unknown costs retain historical
bounds; active reservations cross midnight. Authentication failures stop paid work
globally until a successful free model inventory. Never waive costs to resume.

## Author once, reuse the corpus

`specs/` has one author JSON per scenario: `pizza_day.json`, `pizza_week.json`,
`pizza_month.json` and `bike_pickup.json`. Day/week/month use the same format:
fixed `week_start`, noise, optional cast, reusable channels, Monday–Sunday maps,
short scenes with inline facts, and natural assistant prompts with English
expectations. Several weeks use a `weeks` array. No top-level fact catalog.

```sh
python -m pacbench.specs
```

This validates every spec offline, including duplicate JSON keys/IDs and source
metadata; it creates no corpus and makes no model calls. A 60-spec structural
test passes. Semantic source and contract reviews remain necessary.

Preparation generates and verifies messages once. Provider execution imports a
frozen corpus and never regenerates it. Prefer `artifacts/corpora/` for local
reusable preparation and `runs/` for selected public execution evidence. The
legacy manifest name `week.json` works for any duration. Keep its sibling files,
including `shared-text.json`, together. Editing a spec does not change old results.

The reusable, repaired medium month is committed at:

`runs/pizza-month-local-20261105/preparation/week.json`

It contains 2,868 messages, 48 scenes, 30 nightly imports for September 15–October
14, and 15 final questions on October 19. Questions concern upcoming plans only.
The simulated calendar is fixed: do not move it to the host's current date.
Source-only audits checked the repaired truth; older ambiguous-month date-error
attributions were withdrawn. Do not combine them with these results.

Both this preparation and the Bike preparation were verified from a Git-only
archive. They can be reused without writer/verifier calls. Original private
provider homes/stores are absent, so full native re-audits or interrupted-store
resumption cannot be reconstructed from the public receipts alone.

## Latest saved evidence

`runs/pizza-month-local-20261105` combines source-identical selected components.
These are development diagnostics, not a calibrated ranking. Initial counts:

| Provider | Native ingestion | Pass / fail / ungraded |
| --- | --- | --- |
| Memcal | 30/30 ready | 14 / 0 / 1 |
| Mem0 | 30/30 ready | 11 / 2 / 2 |
| Hindsight | 30/30 ready | 9 / 3 / 3 |
| Supermemory | 27 ready; day 28 blocked; final 2 unattempted | 0 / 0 / 15 |
| Honcho | 1 ready; day 2 failed; remaining 28 unattempted | 0 / 0 / 15 |

The combined run is **incomplete** and its saved native audit is **fail**, listing
all five incomplete arms. Preparation integrity passed independently. Supermemory
and Honcho executed no recall questions: their ungraded entries are ingestion
failure/dependency skips, not 15 attempted answers each.

Six Memcal/Mem0/Hindsight judgments remain ungraded after malformed upstream JSON,
including their allowed retry. Saved content matches the response; do not repair
brackets, reroll for a favorable grade or silently relabel those answers.

Honcho's latest month had all 52 messages acknowledged on each attempted day,
but day two failed native processing with `StructuredOutputError`. Repeated
responses exhausted the configured 8,192-token deriver allowance, mostly in
reasoning. Its native 1,500-token summary cap also failed in an earlier attempt.
These are model/processing compatibility failures before recall, not recall misses.
The selected attempt cost $0.0156698, excluding older attempts and benchmark work.

`runs/bike-pickup-native-clock-20261105` is the small Honcho clock canary: 11
messages, two imports, five checks. Four initial passes; one missed venue was
recovered. Native source-search/background paths prevent a clean retention claim.

OpenViking and ByteRover have no selected current-month arm. Historical blockers
were HTTP 400 `tool_choice` and missing SSE usage respectively. The actual failing
private request evidence was not restored; their causes remain unverified.
Supermemory's day-28 readiness/embedding failure also has no established fix.

## Authenticity and judging boundaries

- Use documented native import/capture and ordinary configuration. No third-party
  provider code patches, rewritten extraction prompts, seeded facts or generated
  assistant ingestion turns. Fix Memcal product bugs in its own repository.
- Memcal consumes synthetic Apple Messages/Contacts and TLS IMAP through its real
  readers. Preserve realistic identities, thread membership, timestamps and email
  reply relationships; never fabricate fields specifically to help Memcal.
- Honcho's 100-message limit is per request. A 300-message thread uses three
  ordered requests in the same native session with its observer enrolled first.
  Acknowledgments establish delivery, not successful processing. Do not force
  native flushes or alter queues to manufacture readiness.
- Disclose configuration choices: Mem0's documented reasoning-model flag,
  Supermemory's `dreaming=instant`, and Honcho's authorized deriver allowance.
  Preserve native batching defaults; do not keep increasing output budgets.
- Hermes has declared harness changes for tool boundaries, virtual time and
  capture. It starts outside Git with built-in memory disabled; OS tools and
  unrelated connectors are not exposed. This is not a full OS sandbox.
- Native source/message search is allowed and logged, but earns zero retention
  credit. Ordinary Memcal event/page opening is expected and retains credit.
  Semantic answer grades and retention are separate; unknown internal paths stay
  uncredited pending review. Observed costs do not establish lifecycle rankings.
- One combined follow-up is allowed only for silent, implicit omissions with at
  least one correct fact. None for wrong values, explicit missed requests,
  admitted retrieval failure or zero recall. Recovered facts earn half credit.
- Judge objective facts invited by natural prompts, not a preferred clarification
  style. A past appointment does not prove attendance. Continue independent
  checks after failures, preserving ungraded causes and dependency skips.

See [provider setup](docs/document-ingestion-provider-map.md),
[authoring](docs/authoring-options.md), [questions](docs/flexible-checks.md) and
[recovery](docs/followups.md) for the implementation contracts.

## Resume in this order

1. Clone PACBench `work`, gateway `main` and current Memcal `main` separately.
   Restore gateway state and configure secrets before inference. Reinstall pinned
   native runtimes using `vendor/README`; several were absent after restoration.
   The active `/workspace/muse-gateway` is now an independent repository.
2. Inspect the explorer and saved evidence first. Reuse the committed preparation.
   Current `--prepared` input is the month manifest above; use an explicit message
   bound such as `--max-messages 3000`. Choose a fresh output directory for each
   trial, never overwrite source evidence or reuse a failed native store blindly.
3. Investigate judge JSON reliability with a small controlled diagnostic. A prior
   JSON-mode diagnostic succeeded, but no general fix was enabled. Version any
   judging change and keep old grades intact.
4. Run small compatibility canaries for Supermemory, OpenViking and ByteRover.
   The gateway now records safe request-shape labels and usage-field types, and
   handles case-insensitive SSE MIME types. Inspect actual failures before changing
   protocol handling. It does not drop tool choices or waive missing usage costs.
5. Keep Honcho's structured-output compatibility issue separate; do not repeat the
   same failing month or enlarge budgets without new evidence. Its virtual-clock
   condition is `honcho-wall-clock-v3`; follow the native-clock rules in AGENTS.
6. Once canaries work, rerun the same medium month across the configured providers,
   audit tools/judgments/costs, then expand gradually. Do not generate 60 long
   corpora while infrastructure and judging still have known blockers.

The month currently has about 26.5 MB in 1,187 public files. The four author specs
together are about 54 KB. Generated evidence is automatic, not more hand authoring.
Before scaling, address duplicated corpus copies through immutable references and
portable publication; preserve exact tool results/judgments. Avoid another schema
framework or deleting evidence merely to reduce file count.

## Verification and working habits

Last full code checks: PACBench **662 passed, 6 skipped**; gateway **56 passed**.
Standalone gateway wheel builds successfully. Skips are explicit missing optional
native/live prerequisites, not simulated passes. These checks are not live model
or provider-quality validation.

```sh
python -m pytest -q
python -m pacbench.preparation_audit runs/pizza-month-local-20261105/preparation
node --check explorer/dashboard.js
node --check explorer/timeline.js
python3 serve_explorer.py
```

Cloud sandbox tests may require loopback access and a writable temporary directory
outside Git (`TMPDIR=/var/tmp` worked here). Never weaken source isolation to make
tests pass. Verify the vendored Hermes tree exactly matches its tracked pin/patch.
Honcho SDK requirements now match Hermes' **2.2.0** pin, not the old 2.7.0 override.

SOL makes implementation decisions and writes code. Delegate large code/log
investigations and run audits to GPT-6.1 Terra at medium reasoning; Luna can do
simple retrieval. Use focused read-only briefs and make final decisions in the
main agent. Explain findings and choices in chat, maintain this requested handoff
and existing operating docs, and commit/push coherent work.

On the user's Mac, pull `work` and run `python3 serve_explorer.py`. Cloud localhost
is separate from the Mac. The explorer reads tracked `runs/` and shows native
tools/results, per-message criteria/judgments, source paths and observed costs.
