# Changes from baseline

What our actual setup does differently from the planning documents in `Guide and roadmap/`
(Winning Roadmap, Claude Prompt Pack, Setup and Workflow Manual). Where they disagree, this file
and the repo win. Add a line here whenever we deviate again during the day.

Date: 21 Nov 2026. Scope so far: base files (Manual section 2.2, with content from Parts 3-5);
clinical build A1 (NEWS2) and A2 (artefact, trend, queue) in section 7.

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

## 7. Clinical build (Teammate A, `feat/clinical`)

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
