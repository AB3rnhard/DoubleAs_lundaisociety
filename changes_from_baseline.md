# Changes from baseline

What our actual setup does differently from the planning documents in `Guide and roadmap/`
(Winning Roadmap, Claude Prompt Pack, Setup and Workflow Manual). Where they disagree, this file
and the repo win. Add a line here whenever we deviate again during the day.

Date: 21 Nov 2026. Scope so far: base files (Manual section 2.2, with content from Parts 3-5);
clinical build A1 (NEWS2), A2 (artefact, trend, queue) and A3 (patient view) in section 7.

## 1. Repo, branches, machine

| Baseline | Actual | Why |
|---|---|---|
| Repo `sjukhus-hemma-copilot`, created with `gh repo create` | Existing repo `DoubleAs_lundaisociety` | Already created and shared |
| First commit straight to `main` | Base files committed on `initial-shell`, then PR/merge into `main` by a human | Keep `main` clean until the skeleton is reviewed |
| `main` + `feat/clinical` + `feat/ops` | Same, after `initial-shell` is merged: both feature branches start from `main` | `initial-shell` is for setup only |
| Python 3.12 (Manual) / 3.11 (Prompt Pack, Roadmap section 10) | Python 3.12 (`py -3.12`) | OR-Tools wheels; Manual is the newer document |
| `make`, `uv`, `gh` available | Windows laptop without them: plain `venv` + `pip`; `tasks.ps1` mirrors the Makefile; PRs via the GitHub website | Tools not installed |
| Data pack from `sjukhus_hemma_datapack.zip` | Copied from `Sjukhus Hemma Synthetic Data Pack/` (git-ignored) into `data/`, unchanged | Pack was already unzipped in the repo folder |

## 2. Files added that the baseline did not list

| File | Why |
|---|---|
| `pyproject.toml` | `pytest` needs `pythonpath = ["."]` to import `core`, `clinical`, ... from `tests/`; without it CI fails. Also holds ruff config (excludes the copied generator, `Test/`, guide and data-pack folders) |
| `tasks.ps1` | Windows equivalent of the Makefile (`.\tasks.ps1 run|test|lint|check|demo`) |
| `.gitattributes` | Avoids CRLF/LF diffs between Windows and macOS teammates |
| `BRIEF.md` | Was only a rule in Manual Part 8; created now with placeholders, decision pre-filled "Plan A confirmed" |
| `tests/test_contracts.py` | pytest with zero tests exits with an error, which would make CI red on the first push |
| `scripts/generate_datapack.py` | Copy of the data-pack generator, `OUT` changed to write into repo `data/`; kept for the "can you regenerate the data?" question. Excluded from ruff |
| `changes_from_baseline.md` | This file |

## 3. Changed content

| Area | Baseline | Actual | Why |
|---|---|---|---|
| `app.py` | Thin file with sidebar clock; pages auto-discovered from `pages/` | Router using `st.navigation([...st.Page...])`; sets logging, session-state defaults, sidebar clock, then `pg.run()` | With auto-discovered pages `app.py` only runs on the home page: the clock slider disappears on other pages and Streamlit drops the `clock` widget state on navigation |
| Pages | Free to configure themselves | Pages never call `st.set_page_config` | Only the entry file may set it under `st.navigation` |
| `core/loader.py` | Written at 09:45 with prompt P0 part 3 | Written in the setup commit: one `load_*()` per file, `load_all()`, `at_clock(df, clock)`, all opens with `encoding="utf-8"` | Data was available; gives both teammates a working base and a real test |
| `tests/test_loader.py` | Row counts for 5 files | Row counts for all 8 tables, `decisions_template` columns, `at_clock(vitals, T0)` = all history rows, expected_results keys | Catches a broken copy of the data pack |
| `core/contracts.py` | Manual Part 4 version | Identical + `if __name__ == "__main__":` block | Project rule: every module has one |
| `run_whatif` return type | `list[Plan]` (Roadmap 6.1, Prompt Pack B2) | `Plan` (one call per profile) | Follows the Manual / contracts file |
| `reasons` type | `list[str]` (Roadmap, P0) | `list[Reason]` Literal | Follows the Manual / contracts file |
| Feature modules | Built by P0 as "empty modules with docstrings" | Stubs: docstring (purpose, owner, prompt that builds it, target signature), logger, `__main__` print. No function bodies | Owners define signatures in their own prompts (A1-A5, B1-B5) |
| Empty test files | Listed in the tree | Docstring only (owner + prompt), no placeholder/skipped tests | Keeps test output clean |
| `.gitignore` | Manual list | Manual list + existing `Test/`, `Guide and roadmap/` + `Sjukhus Hemma Synthetic Data Pack/`, `.pytest_cache/`, `.ruff_cache/` | Keep local folders and caches out of git |
| `.claude/settings.json` | Bash allow/deny rules | Same + PowerShell equivalents (`.\tasks.ps1 *`, `.venv\Scripts\python *`, deny `git push --force`, `Remove-Item -Recurse`) | Claude Code on Windows runs commands through PowerShell |
| `core/contracts.py` imports | `from dataclasses import dataclass, field, asdict` | `from dataclasses import asdict, dataclass, field`, plus a blank line after `from __future__ import annotations` | The installed ruff (0.17) flags unsorted imports (I001); CI runs `ruff check .` without `--fix`. Applied with `ruff --fix`. No behaviour change |
| `core/loader.py` `load_notes()` | (not specified) | Reads `notes.jsonl` with `json.loads` per line instead of `pd.read_json` | pandas 3 deprecates `read_json(convert_dates=...)`; plain json keeps `ts` parsing explicit |
| `README.md` | New file | Existing title kept; added product summary, disclaimer, Windows + macOS setup, commands | Repo already had a README |

## 4. `CLAUDE.md` edits vs Manual Part 3

- Added a **Branches** section (main = integration, feat/clinical, feat/ops, Claude never pushes or merges).
- Stack line: "st.navigation router in app.py, page files in pages/".
- Shared files now also include `pyproject.toml`; `ai/schemas.py` and `ai/prompts/` are append-only (each owner adds their own schema/prompt).
- Interfaces: added `core.loader` (`load_*()`, `at_clock`) and "never read data/ files directly elsewhere".
- Rules added: tests never call the real API; clock slider lives in `app.py`; pages never call `st.set_page_config`; `print` only inside `__main__`; open text files with `encoding="utf-8"`.
- Commands: Windows `.\tasks.ps1 ...` next to `make ...`; `python -m <module>` to run a module alone.
- Pointer to `BRIEF.md` in the Product section.

## 5. Deviations found while building the setup commit (Prompt 0)

| Area | Prompt said | Actual | Why |
|---|---|---|---|
| Stub docstrings: prompt that builds each module | "A1-A5, B1-B5, I2" without a per-file mapping | Mapped from the Prompt Pack headings: news2 = A1; artefact, trend, queue = A2; views = A3; factsheet, verifier, claude_client = A4; triage = A5; eligibility, capacity = B1; whatif_cpsat, greedy = B2; explainer = B4; ledger = B5; schemas = A4/B4; warm_cache = I2 | The prompt left the mapping open. `claude_client` and `ledger` are my best guess (A4 mentions both); correct if the prompt pack says otherwise |
| `pip install` | `pip install -r requirements.txt` | First run hit a DNS failure (`files.pythonhosted.org`) and stalled; I killed it and re-ran with `--retries 10 --timeout 60`, which succeeded | Transient network problem. Resolved versions: pandas 3.1.1, numpy 2.5.5, streamlit 1.67.0, ortools 9.15, anthropic 2.12, ruff 0.17.6, pytest 9.2 |
| `tests/test_loader.py` | Listed tests | One extra test, `test_load_all_keys` | Cheap check that `load_all()` returns the main keys |
| `core/loader.py` `load_notes()` | Not specified | Parsed with `json.loads` per line (see section 3) | pandas 3 deprecation warning |
| Streamlit smoke test (step 5.4) | Health check, and a browser check of the 4 sidebar pages and the clock slider if a browser tool exists | Health check returned `ok` and the server log had no tracebacks. No browser tool available, so the sidebar and slider were checked with `streamlit.testing.v1.AppTest` instead | See open question below |
| `.vscode/` | Not mentioned | An untracked `.vscode/settings.json` (`python.terminal.useEnvFile`) appeared in the working tree, created by the editor, not by this setup. Left out of the commit | Not part of the specified tree |

### Resolved: clock slider on non-default pages

Checked in a real browser after the merge into `main`: the slider shows on all 4 pages, and a clock
set to 13:00 on Command Center stays 13:00 on Capacity & What-if and Ledger. No server errors. The
`KeyError` came from `AppTest` running the page file without `app.py`, not from the app. Opening a
page URL directly in a new tab starts a new session at 09:00, which is expected. No change needed.

Original note:

`AppTest` loaded the home page fine (4 pages registered, slider and `clock` state present). After `switch_page(...)` to another page, the re-run showed no sidebar slider and dropped the `clock` key, so pages 3, 4 and 1 raised `KeyError: "clock"`. It is not yet known whether this is a real problem in `app.py` or an `AppTest` artefact (it may run the page file directly instead of `app.py`). Please check in a real browser (`.\tasks.ps1 run`, click through all 4 pages, confirm the slider stays and the clock value is kept). If the slider disappears, `app.py` and the pages need adjusting in a separate "contracts: ..." commit.

## 6. Not changed (on purpose)

- Product scope, MoSCoW, storyline, acceptance numbers (`data/expected_results.json`).
- Contracts fields and fakes.
- `requirements.txt` and `.env.example` content.
- CI workflow (`.github/workflows/test.yml`) content.
- Data files: copied byte-for-byte, never edited.

## 7.B Deviations found while building B1 (ops/eligibility, ops/capacity)

| Area | Prompt said | Actual | Why |
|---|---|---|---|
| Branch | "We are on main", create `feat/ops` | Repo was on `branchB`; `feat/ops` created from it (same commit as `main`) | Local branch name differed |
| NEWS2 source | `clinical.news2` (A1) | `ops/_news2.py` temporary fallback (`score`, `score_df`) plus `resolve_score_df()`, which prefers `clinical.news2.score_df` and logs which one it used | `clinical/news2.py` is still a stub. Remove the fallback after the 12:45 merge once tests pass with A's version |
| `eligibility.transfer_risk` | `transfer_risk(vitals_scored, clock)` | Extra optional `patients` argument | Lets `capacity.py` avoid reloading `patients.csv` |
| `eligibility.load_scored(clock)` | Not specified | Helper returning (patients, scored vitals, plans, notes) up to `clock` | Shared by the `__main__` blocks and tests; B2/B3 can reuse it |
| Capacity cross-check | "warn if slots differ" | Warning logged if `resources.json` virtual-ward `occupied` differs from the VIRTUAL_WARD row count in `patients.csv`; at T0 they agree (14) | As specified |
| No-patient-specific-logic test | Regex finds no `"P-/I-/A-` + digit in the source | Regex applied to the source before the `if __name__ == "__main__"` block | The `__main__` demo uses `escalated=["P-002"]` as the prompt required |
| Rule-vs-data conflicts | Ask before special-casing | None: all 12 inpatients match `hah_status`; step-down set is exactly P-005 and P-009 | Rules reproduced the data as written |

Contract expected from A's `clinical.news2.score_df(vitals, plans)`: input columns
`patient_id, ts, hr, spo2, rr, sbp, temp_c, consciousness, on_oxygen, signal_quality` and plan columns
`patient_id, spo2_scale`; output = same rows plus int columns `news2` and `news2_max_single`.

## 8.B Deviations found while building B2 (ops/whatif_*, ops/greedy)

| Area | Prompt said | Actual | Why |
|---|---|---|---|
| Signature | Prompt Pack: `run_whatif(n, ward_state, profile, escalated=[])` | `run_whatif(n, ward_state, profile)`; escalated patients come from `ward_state.escalated` | Follows CLAUDE.md / `core/contracts.py` |
| New module | Pack lists only `whatif_cpsat.py` and `greedy.py` | Added `ops/whatif_model.py`: `build_inputs`, `evaluate_plan` (plain-Python checker of every rule), `make_plan`, `Decisions`, `WhatIfInputs` | Shared by both solvers and the tests; the checker post-checks every CP-SAT result |
| Extra public functions | `run_whatif`, `shadow_price` | Also `run_all_profiles(n, ward_state)` (builds inputs once, solves 3x) and `solve_inputs(inputs, profile)` in both solver modules | Keeps the 3-profile view under 2 s |
| `shadow_price` | `resource="kits"` | Supports `"kits"` and `"beds"`; other values raise `ValueError`. Returns placed(+1 unit) minus placed(baseline) | As specified in the B2 task |
| Formulation source | Pack text | Ported from the brute-force reference in `scripts/generate_datapack.py` (read, never imported); overflow only counts if actually filled; escalated patients release slot, visits and kit | These semantics are what make the expected numbers match |
| CP-SAT result | "Require OPTIMAL" | OPTIMAL required in practice; FEASIBLE is returned with a warning; INFEASIBLE or missing `ortools` falls back to `greedy.solve_inputs` (plan `solver="greedy"`) | Per task |
| Greedy quality | "feasible heuristic" | Always feasible (all-external baseline + home-first/beds-first construction + prune). Matches CP-SAT in 4 of 6 reference cases; worse on `max_capacity` (-0.20 vs -1.15; 5.75 vs -0.20) | Fallback only; not tuned |
| Planned discharge already due | Not specified | If `planned_discharge_at <= clock` the bed counts as free (logged); only future discharges are expedite candidates | Task rule |
| Unknown escalated id | Not specified | Ids that are not virtual-ward patients are ignored with a warning; `needing_placement = n + valid escalated` | Avoids a crash |
| No-patient-specific-logic test | Regex finds no `"P-/I-/A-` + digit | Applied to the whole of `whatif_model.py`, `whatif_cpsat.py`, `greedy.py` (including `__main__`, which takes the readmitted id from `fake_ward_state().queue[0]`) | Stricter than B1 |
| Reference mismatches | Stop and report if any | None: all 6 objectives and plans match `expected_results.json`; at 14:00 with P-002 readmitted the counts match the README story (5/6, 6/6, 6/6 + 1 spare) | - |

Shadow price of kits under `safety_first` is 1 in both scenarios (README: "the binding constraint is monitoring kits").
`run_all_profiles(5, ...)` takes about 0.1-0.6 s. `fake_ward_state` defaults to clock 13:00, so tests always pass the clock.
## 7.A Clinical build (Teammate A, `feat/clinical`)

### A1 - `clinical/news2.py`

| Area | Prompt / spec | Actual | Why |
|---|---|---|---|
| Plan fields used by NEWS2 | Plan-aware NEWS2 | Only `spo2_scale` is read from the monitoring plan; `hr_high`, `sbp_low`, ... are not used | They belong to PLAN_BREACH in `clinical/queue.py` (A2) |
| `score_df` filters | (not specified) | No clock filter and no signal-quality filter inside `score_df` | Callers use `core.loader.at_clock`; the artefact filter lives in `clinical/artefact.py` |
| Patient without a plan | (not specified) | Scale 1, one `logger.warning` per such patient | Safe default, visible in `logs/app.log` |
| SpO2 Scale 2 on oxygen | RCP 2017 table | The "on oxygen" Scale 2 bands apply only when on oxygen and SpO2 >= 93; below 93 the "air" table applies | RCP table: 88-92 scores 0 even on oxygen |

### A2 - `clinical/artefact.py`, `trend.py`, `queue.py`, Command Center page

| Area | Prompt / spec | Actual | Why |
|---|---|---|---|
| Artefact reading in the data | `data/README.md`: P-011 at 12:00 has HR 118 | `vitals.csv` and `expected_results.json` have HR 113 (SpO2 76, quality 0.22) | Tests follow `expected_results.json`; the README text is slightly off |
| ISO helper | Timestamps returned as `YYYY-MM-DDTHH:MM` | `to_iso()` lives in `clinical/artefact.py` and is imported by `trend.py` and `queue.py` | Only the five A2 files may be edited; avoids three copies |
| `detect()` output | ISO-string timestamps | `ts` column is an ISO string; `queue.py` converts it back to a Timestamp internally. `baselines()` windows are ISO strings too | Follows the ISO rule; Timestamps stay internal |
| `detect()` scope | Every patient in the input | Includes inpatients (4-hourly readings); callers filter to `P-` | Keeps the function data-driven |
| `deviations()` | Signature includes `messages` | `messages` accepted but unused (deviations depend on vitals only); result in parameter order hr, rr, spo2, temp_c, sbp, not sorted by z | Stable interface for A4; ordering was unspecified |
| Missing `signal_quality` | (not specified) | Counted as good (`< 0.5` is False for NaN) | The data has no missing values |
| `suppressed_alerts` / `alert_burden()["suppressed"]` | Readings that alert naive but not plan-aware | Counted per reading (naive alert and not plan-aware alert); equals naive minus plan-aware on this data (58 at 21:00) | Matches the spec wording; tests assert the equality |
| `NEWS2_URGENT` | NEWS2 >= urgent threshold or `news2_max_single == 3`, not emergency | Same; emergency is evaluated first, so the two chips are exclusive | Already decided in the prompt |
| `get_ward_state` input/output | `clock` ISO string or Timestamp | `WardState.clock` is always an ISO string; `escalated` is copied so the caller's list is never shared | Contract says `clock: str` |
| Extra tests | Listed in the prompt | Added: cut-data vs full-data `detect()` equality at every hour (no look-ahead), ARTEFACT reason clears when the next reading is good, artefact excluded from the baseline, symptom window edges (same timestamp counts, 3 h is outside) | Cheap guards for the main risks |
| Command Center page | Minimal, real data | `st.cache_data` wrapper takes `escalated` as a tuple; shows the suppressed metric, the queue table, the artefact table or "No artefacts". Smoke-tested with `AppTest` only (not in a browser) | Layout and colours come later |
| Python for ad-hoc commands | Python 3.12 | The default `python` on this laptop is 3.10 without pytest; always use `.\tasks.ps1` or `.venv\Scripts\python.exe` | Venv has the project dependencies |

### A3 - `clinical/views.py`, `queue.readings()`, Patient page

| Area | Prompt / spec | Actual | Why |
|---|---|---|---|
| Plan lines and baseline lines in `vitals_figure` | `add_hline` / `add_hrect` | Drawn as layout shapes (`xN domain` / `yN` refs) instead of `add_hline(row=...)` | The `row=` form took about 800 ms per figure; the page must load in under 1 s |
| Amber trend markers | "On the rows whose z >= 2.5 if easy" | Done: a trend/symptom marker appears only on rows where that reading's robust z is at least 2.5 | Easy with the cached baselines |
| `comorbidities` in the header | (type not specified) | List of strings, split on `;`. `custom_rule` and `caregiver_at_home` are `None` when empty | Easier to render; matches "None if empty" |
| Unknown pid | `readings` returns an empty frame; header not specified | `patient_header` raises `KeyError` | A wrong id should fail loudly in the header |
| NEWS2 figure window | "24 h up to the clock" | Readings with `ts >= clock - 24 h`, inclusive, so at 21:00 the 21:00 reading from the previous day is a bar | Inclusive boundary; tests compare stream hours only |
| Selector and deep link | `st.query_params["pid"]` default | An `on_change` callback writes the selection back to `st.query_params["pid"]` | Otherwise the query param overrides the user's selection on every rerun |
| Reason chips and timeline badges | "Small coloured labels" | Streamlit `:red-badge[...]` / `:orange-badge[...]` / `:gray-badge[...]` markdown, no custom HTML; `$` escaped in note text | Safe and no `unsafe_allow_html`; avoids LaTeX rendering of dollar signs |
| Page smoke tests | `AppTest.from_file("pages/...")` | Absolute path built from the repo root | Streamlit 1.67 resolves relative paths against the calling test file (`tests/`) |
| `get_briefing` | Interface in CLAUDE.md | Not added to `views.py` in A3; module docstring says A4 | As instructed in the A3 prompt |
| First page load | Under 1 s after first load | First Patient render about 3 s in AppTest (cold data load); later renders about 0.07 s; `python -m clinical.views` per-call total about 220 ms | Cold load is the one-off scoring of all readings, shared with the Command Center |

### A4 - factsheet, verifier, briefing, decisions

Files: `ai/claude_client.py` (shared), `ai/schemas.py`, `ai/prompts/briefing_v1.md`, `ai/factsheet.py`,
`ai/verifier.py`, `clinical/decisions.py`, `pages/2_Patient.py`, `ai/cache/`, tests. Branch `feat/clinical2`.

| Area | Prompt / spec | Actual | Why |
|---|---|---|---|
| Decisions storage | CLAUDE.md: `core.ledger.record(Decision)` | `clinical/decisions.py` calls `core.ledger.record` if it is callable, otherwise appends one JSON line to `logs/decisions_pending.jsonl` (env `DECISIONS_PENDING_PATH`). `list_pending()` reads it back, newest first | B's `core/ledger.py` is still a stub and is not ours to edit. `logs/` is git-ignored, so the pending file stays on the machine that wrote it |
| AI cache | `core.ledger.cache_get/cache_put` (CLAUDE.md, `ledger.db`) | One JSON file per key in `ai/cache/` (file name `sha256(key)[:24].json`, env `AI_CACHE_DIR`), behind the private `_cache_get/_cache_put` in `ai/claude_client.py`. Folder is committed | The ledger does not exist yet, and `ledger.db` is git-ignored so `DEMO_MODE=1` would not work from `main` |
| Briefing status | Pack 3.1 rule 6: the model derives `status` | Rule 6 is "copy the rule-based status given in the user message"; the user message has a `Rule-based status:` line after `Queue reasons`; the verifier rejects any other status | The status must not drift from the queue rules, and the model cannot be wrong about it |
| No-new-numbers rule | Pack 3.1 rule 2: "Do not compute new numbers" | Rule 2 also says "do not write any number (including durations and counts) that is not in the evidence you cite". The verifier checks every number against the cited items | Durations such as "rising for 5 hours" were the likely failure; they are now rejected |
| `max_tokens` and thinking | (not specified) | `max_tokens` 4000, effort `"low"`, no `thinking` / `temperature` / prefill; the first `text` block is used | Haiku 5.5 thinks by default, so the response can start with thinking blocks. Live check: about 2.4k output tokens per answer |
| `get_briefing` location | Prompt Pack: `clinical.views` / `ai.factsheet` | In `ai/factsheet.py`. `clinical/views.py` only got a docstring note | `ai.factsheet` imports `clinical.views.patient_header`; the other direction would be an import cycle |
| `call_json` | `call_json(system, user, schema, model, cache_key)` | Same plus keyword-only `effort`, `max_tokens`, `prompt_version`, `cache_only`; classes `ClaudeError` and `CacheMiss`; extra public `cache_drop(key)` | `cache_only` backs `live=False`; `cache_drop` lets a failed live run clear its own bad cache entries so the next "Generate briefing" asks Claude again |
| Retry and cache order | "Retry once when live" | Attempt 1 uses key `K`, attempt 2 uses `K\|retry`. Without `live`, the cache is read for both keys, so a verified retry answer is found again later. After two failures on a live call, both entries are dropped | Otherwise the first (invalid) answer would be cached under `K` and hide the valid retry in demo mode |
| Verifier scope | Checks listed in the prompt | Schema-agnostic. Evidence = any list of dicts with `id` and `text`. Claims = dicts with `evidence_ids` plus every plain string (headline, `status`, each `missing_information` entry), so a briefing is counted e.g. "30 of 30". Digits glued to letters on either side (`SpO2`, `6h`, `500mg`) are not numbers; doses are matched separately and case-insensitively. A claim with no cited item skips the number check (the id error already fails it) | Reusable by B4; one rule for numbers |
| Factsheet extras | Fact = `id`, `type`, `ts`, `text` | Some facts carry internal fields (`triggered` on plan facts; `param`, `value_text`, `baseline_text` on deviation facts). They are not sent to Claude and not in the cache key | `deterministic_briefing` must build a briefing from the factsheet alone |
| Plan facts | One fact per plan rule | Also: "NEWS2 urgent threshold 5 (or any single parameter scoring 3)"; a custom rule is "Plan custom rule (not machine-checked): ..."; "Plan set by ..." is a fact but not a rule. In the rule-based fallback a custom rule is always `not_triggered` and "set by" is skipped | The custom rule is free text and cannot be evaluated by code; the label says so |
| Vital / artefact facts | `"13:00 HR 96, SpO2 94, RR 20, SBP 124, Temp 37.9, on oxygen no"` | Same plus `, consciousness A`, and `on oxygen yes (2 L/min)` when on oxygen. Window is `clock - 6 h < ts <= clock` (6 hourly readings); artefacts `clock - 24 h < ts` | Consciousness is a schema parameter; the exclusive lower bound gives exactly 6 readings |
| `deterministic_briefing` | Summary not specified | `summary_points` = the queue fact and the NEWS2-now fact (each cited); `plan_rules` for every rule fact; `relevant_history` = latest note + symptom messages (full text); no actions, nothing missing | A fallback with an empty summary looks broken; it passes `verify` for every patient at 09:00, 13:00 and 21:00 |
| `Decision` fields | `ts`, `reason`, `evidence_ids` | `ts` = simulation clock (not wall time); `item_id` = `BRF-<pid>-<HHMM>`; `ai_suggestion` = compact JSON `{"source", "briefing"}`; for `edit` the clinician's text is stored in `reason`; `evidence_ids` = sorted unique ids cited in the shown briefing (plain string sort, so F11 comes before F2) | `Decision` has no field for edited text |
| Patient page | "Generate briefing" calls `live=True` and reruns | The click runs `get_briefing(live=True)` in the same script run (spinner, no `st.rerun`, no extra `st.session_state` key); later runs read it from the cache. Form widget keys are `briefing_*_<pid>_<clock>`, so a new patient or clock gets a fresh form. Edit and Reject need text, otherwise `st.error` and nothing is written. Changes vs baseline is `st.table` (no hover on the evidence column) | Keeps shared state to `clock`, `escalated`, `tasks`; a decision belongs to one briefing |
| Existing A3 test | "`tests/test_views.py`: add tests only" | `test_patient_page_default_is_top_of_queue` now reads `at.selectbox(key="patient_pid")` instead of asserting `len(at.selectbox) == 1` | The decision form's "Role" select is a second selectbox |
| Test fixtures | (not specified) | Each test file sets `DEMO_MODE=1`, `AI_CACHE_DIR` and `DECISIONS_PENDING_PATH` to `tmp_path` in its own autouse fixture; no `tests/conftest.py` | `conftest.py` is not on the A4 file list |
| Live check | One live call for P-002 at 13:00 | `source=claude`, `retried=True`, 30 of 30 claims verified, headline "Rising RR and temperature with new breathlessness and shivering; review needed now". The first answer failed 4 checks (numbers 97, 20, 21, 19 not in the cited evidence), so there were 2 API requests (about 13 s each, 4.0k in / 2.4-2.7k out tokens each). Both answers are in `ai/cache/` (the invalid first one is never shown; it is needed so demo mode finds the retry) | Shows the verifier and retry work on real output |
| Timings | Page < 1 s after first load | `get_briefing(live=False)` about 10 ms with a cached answer, 3 ms rule-based. Patient page render 0.46 s on the second run in `AppTest`; first render in a fresh process about 10 s (imports of `anthropic`, plotly and the one-off scoring) | A3 measured about 3 s cold; the extra is the new imports |

### Realignment with Teammate B (open)

- [ ] 1. Decisions: `clinical.decisions.record_decision` switches to `core.ledger.record` automatically once it
  exists. One-off step after B's ledger lands: import `logs/decisions_pending.jsonl` into the ledger with
  `list_pending()` + `ledger.record()`, then delete the file.
- [ ] 2. AI cache: lives in `ai/cache/*.json` (committed, so `DEMO_MODE=1` works from `main`; `ledger.db` is
  git-ignored). Decide together whether `core.ledger.cache_get/cache_put` wrap this folder or replace it; either
  way the change is in `_cache_get/_cache_put` (and `cache_drop`) in `ai/claude_client.py` (one `contracts:` commit).
- [ ] 3. B4 explainer: call `call_json(..., prompt_version="explainer_v1")` and
  `verifier.verify(output, {"facts": [P# facts]})`; no `expected_status`. Evidence needs `id` and `text`.
- [ ] 4. B5 Ledger page: read decisions from `core.ledger.list_decisions()` and, until item 1 is done, also
  `clinical.decisions.list_pending()`. Briefing rows have `item_type="briefing"`, `item_id="BRF-<pid>-<HHMM>"`,
  `ai_suggestion` = JSON with `source`.
- [ ] 5. Escalation: `st.session_state["escalated"]` is filled by the Patient page form; B3 reads it (no change
  needed, listed for the I1 story check).
