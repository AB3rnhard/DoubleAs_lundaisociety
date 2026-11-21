# Sjukhus Hemma Copilot - hackathon build (9 h, 2 devs, parallel)

## Product
Decision-support prototype for a hospital-at-home virtual ward (fictional, modelled on Skane
University Hospital "Sjukhus hemma"). One loop: monitor home patients -> flag deterioration early
-> Claude drafts evidence-cited briefing -> verifier checks -> clinician approves/edits/rejects ->
decision updates capacity what-if (+N admissions) -> 3 feasible plans with sign-offs -> ledger.
AI never decides. Every AI sentence cites evidence IDs. Challenge brief and criteria: BRIEF.md.

## Stack
Python 3.12, Streamlit (st.navigation router in app.py, page files in pages/), pandas, numpy,
plotly, ortools CP-SAT, anthropic, python-dotenv, sqlite3, pytest, ruff.
No new dependencies without asking.

## Branches
main = integration branch (humans merge). Teammate A works on feat/clinical, Teammate B on
feat/ops, both branched from main. Claude never pushes, force-pushes or merges.

## Ownership (do not edit files you do not own; ask instead)
Teammate A (feat/clinical): clinical/*, ai/factsheet.py, ai/verifier.py, ai/triage.py,
  pages/1_Command_Center.py, pages/2_Patient.py, tests for those.
Teammate B (feat/ops): ops/*, core/ledger.py, ai/explainer.py, pages/3_Capacity_WhatIf.py,
  pages/4_Ledger.py, tests for those.
Shared (change only in a separate commit "contracts: ..." after both agree): core/contracts.py,
  core/loader.py, ai/claude_client.py, app.py, CLAUDE.md, requirements.txt, pyproject.toml.
ai/schemas.py and ai/prompts/: append-only; each owner adds their own schema / prompt file.

## Interfaces (core/contracts.py is the single source of truth)
clinical.queue.get_ward_state(clock) -> WardState
clinical.views / ai.factsheet: get_briefing(pid, clock, live=False) -> dict
ops.capacity.get_capacity(clock, ward_state) -> dict
ops.whatif_cpsat.run_whatif(n, ward_state, profile) -> Plan   (ops.greedy same signature)
core.ledger.record(Decision) ; list_decisions() ; cache_get(key) ; cache_put(key, ...)
core.loader: load_*() functions + at_clock(df, clock) ; never read data/ files directly elsewhere.
Until the real function exists, use core.contracts.fake_ward_state() / fake_plan().

## Data (data/) - never edit
T0 = 2026-11-21T09:00; phase history (<= T0) / stream (to 21:00). See data/README.md.
Acceptance numbers: data/expected_results.json. Tests must read numbers from it, not hard-code.

## Rules
- Small diffs. After every change run the tests (make test / .\tasks.ps1 test). Never commit
  failing tests.
- No patient-specific logic (no `if pid == "P-002"`).
- Every module has `if __name__ == "__main__":` that prints a quick result for debugging.
- All Claude calls via ai.claude_client.call_json(system, user, schema, model, cache_key).
  Default model from env CLAUDE_RUNTIME_MODEL (claude-haiku-5-5), effort "low".
  DEMO_MODE=1 -> read cache only. Tests never call the real API. Never show AI output that
  failed ai.verifier.
- Streamlit: shared state only via st.session_state keys: "clock", "escalated", "tasks".
  The clock slider lives in app.py (runs on every page). Pages never call st.set_page_config.
  Cache data loads with st.cache_data. Page must load < 1 s.
- Log with logging to logs/app.log (logger = logging.getLogger(__name__)). No print in app code
  (print only inside `if __name__ == "__main__":`).
- Open text files with encoding="utf-8" (Windows default is cp1252).
- UI: white background, accent #0F766E, red = urgent, amber = review, grey = suppressed.
- When a spec detail is unclear, read data/README.md and expected_results.json, then ask me.

## Commands
macOS/Linux: make run | make test | make lint | make demo | make check (lint + test)
Windows:     .\tasks.ps1 run | test | lint | demo | check
Run a module alone: python -m clinical.queue (each module has a __main__ block)
