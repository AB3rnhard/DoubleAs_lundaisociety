# DoubleAs_lundaisociety

Sjukhus Hemma Copilot is a decision-support prototype for a hospital-at-home virtual ward
(fictional, modelled on Skane University Hospital "Sjukhus hemma"). One loop: monitor home patients,
flag deterioration early, have Claude draft an evidence-cited briefing, check it with a verifier,
let the clinician approve, edit or reject, feed the decision into a capacity what-if (+N admissions),
get 3 feasible plans with sign-offs, and keep everything in a ledger. The AI never decides, and every
AI sentence cites evidence IDs.

Research prototype. Synthetic data. Not for clinical use.

## Setup

Windows (PowerShell, Python 3.12):

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env   # then paste your own ANTHROPIC_API_KEY into .env
```

macOS / Linux:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then paste your own ANTHROPIC_API_KEY into .env
```

## Commands

```
macOS/Linux: make run | make test | make lint | make demo | make check (lint + test)
Windows:     .\tasks.ps1 run | test | lint | demo | check
Run a module alone: python -m clinical.queue (each module has a __main__ block)
```

Data pack description and acceptance numbers: see `data/README.md`.
