# Sjukhus Hemma Copilot - Synthetic Data Pack (v1.0)

Fully synthetic, seeded (`numpy` seed 42), reproducible data for a fictional medical ward with a hospital-at-home ("Sjukhus hemma") virtual ward in the Lund/Malmö area. No real patient data. Names are invented.

Parameters are inspired by public facts about Skåne University Hospital's Sjukhus hemma service (about 50 simultaneous patients, ~6-day stays, visits 17 h/day, portable IV antibiotic pumps) ([Region Skåne, 2026](https://vard.skane.se/en/skane-university-hospital/about-us/news/hospital-at-home-treating-more-patients-and-providing-more-advanced-care/)) and its catchment municipalities ([1177 Skåne](https://www.1177.se/Skane/sa-fungerar-varden/varden-i-skane/vard-i-hemmet-skane/)). Our fictional ward is scaled down (12 beds, 18 virtual slots) so the demo fits on one screen.

Regenerate: `pip install numpy pandas && python generate_datapack.py` (about 12 s; writes `./data`).

## Clock and phases

| Item | Value |
|---|---|
| Demo "now" (T0) | 2026-11-21 09:00 (morning round, the hackathon day) |
| History | T0 − 72 h to T0 (`phase = history`) |
| Live stream | T0 + 1 h to T0 + 12 h = 21:00 (`phase = stream`) - replay hour by hour with a simulation clock |
| Virtual-ward vitals | hourly (wearable + tablet) |
| Inpatient vitals | every 4 h (ward observations) |

## Files (`./data`)

| File | Rows | What it is |
|---|---|---|
| `patients.csv` | 26 | 14 virtual-ward patients (P-001..P-014) + 12 inpatients (I-01..I-12). Diagnosis, ICD-10, comorbidities, municipality, lives_alone, caregiver, consent, SpO2 scale, therapy, visits/day, kit, `hah_status` (inpatients: suitable / needs_assessment / not_suitable / not_eligible_geography / discharge_planned), `stepdown_status` for virtual-ward patients |
| `vitals.csv` | 1,078 | hr, spo2, rr, sbp, temp_c, consciousness (A/C/V/P/U), on_oxygen, o2_l_min, source, `signal_quality` (0-1), phase |
| `monitoring_plans.csv` | 26 | Clinician-set plan per patient: SpO2 scale (1 or 2) and targets, thresholds, NEWS2 urgent/emergency triggers, free-text `custom_rule`, set_by, set_at |
| `notes.jsonl` | 26 | Clinical notes with `note_id` (N-001...) - admission, progress, transfer, home visit |
| `messages.csv` | 15 | Patient/relative tablet chat messages with `message_id` (M-001...) |
| `resources.json` | - | Beds (12, all occupied, max 1 overflow), virtual slots (18, 14 used), kits (17: 14 in use, 2 free, 1 in cleaning until 15:00), 3 mobile teams x 10 visits/day, overtime (+5 visits, 9,500 SEK), primary-care handover capacity 2, transfer-out option, catchment, drive times |
| `admissions_incoming.csv` | 5 | The "+5 urgent admissions" surge (A-01..A-05) with ETA, NEWS2 at ED, bed need, direct-to-home eligibility |
| `demand_forecast.csv` | 24 | Hourly expected admissions for the next 24 h (baseline, before the surge) |
| `decisions_template.csv` | 0 | Schema for the clinician decision ledger |
| `expected_results.json` | - | Acceptance tests: the numbers your build must reproduce |

## The storyline baked into the data

| Patient | What happens | What the product must show |
|---|---|---|
| P-002 Margareta Lind, 78F, pneumonia, IV cefotaxime at home, Lomma | NEWS2 = 0 at 09:00. Fever and breathing rate rise from 10:00, then pulse, then SpO2, then BP. Messages at 12:00 ("more out of breath ... shivery") and 14:00 (husband: "looks pale"). Previous pneumonia (Feb 2025) needed 2 L/min oxygen. | Trend flag 13:00 (12:00 if the message is fused) while NEWS2 is still 0. NEWS2 reaches 5 at 17:00, 7 at 19:00, 10 at 21:00. Lead time 4 h (5 h with message fusion). |
| P-007 Bengt Larsson, 71M, hypercapnic COPD | SpO2 88-91% on air all the time - his normal, documented target 88-92% (Scale 2). | Scale 1 would raise 57 false alerts in 84 h. Plan-aware Scale 2 raises zero. |
| P-011 Per Olsson, 69M, cellulitis | At 12:00 one reading SpO2 76%, HR 118, `signal_quality` 0.22 (patch loosened in shower; message at 12:12). | Do not alert; mark as artefact and request re-measurement. |
| P-012 Birgitta Persson, 76F, permanent AF | Resting HR 95-108 is her baseline; custom plan HR high = 125. | No alerts from her baseline AF. |
| P-005 / P-009 | Ready for step-down to primary care. P-009 lives alone with a fall in 2024. | P-005 = suitable; P-009 = needs clinician assessment. |
| Inpatients | I-03, I-06, I-08, I-10 suitable for transfer home; I-04 (COPD) and I-12 (consent not asked) need assessment; I-11 lives in Trelleborg (outside catchment); I-01, I-05, I-07, I-09 not suitable; I-02 discharge planned 16:00 (could be expedited with sign-off). | Eligibility board with reasons that point to evidence (notes, NEWS2, consent, municipality). |
| Surge | A-01 urosepsis (NEWS2 7), A-02 NSTEMI, A-04 COPD needing O2 titration need beds; A-03 pneumonia (Lund) and A-05 cellulitis (Malmö) can go direct to the virtual ward. | What-if engine produces three feasible plans with trade-offs. |

## Acceptance tests (`expected_results.json`)

1. NEWS2 (RCP 2017 tables, plan-aware scale) for P-002 by hour: 10:00-13:00 = 0, 14:00 = 2, 15:00-16:00 = 3, 17:00 = 5, 18:00 = 6, 19:00-20:00 = 7, 21:00 = 10 ([RCP NEWS2 report](https://www.rcp.ac.uk/media/a4ibkkbf/news2-final-report_0_0.pdf)).
2. Reference trend detector: personal baseline = first 24 h of home readings (skip first 2 h); robust z = (x − median) / max(1.4826·MAD, floor) in the adverse direction; floors hr 3, rr 1.0, spo2 0.8, temp 0.15, sbp 5; flag when ≥ 2 parameters have z ≥ 2.5 on 2 consecutive good-quality readings (`signal_quality` ≥ 0.5). Expected: first P-002 flag 13:00; zero flags for all other 13 virtual-ward patients.
3. Message fusion: one deviating parameter + a symptom message within 2 h → flag. Expected: P-002 at 12:00.
4. Alert burden over 84 h on the virtual ward: naive (Scale 1 for all, every reading, no artefact filter) = 63 alert readings (P-007: 57, P-002: 5, P-011: 1). Plan-aware + artefact filter = 5 (all P-002, all real). That is a 92% reduction with no real deterioration missed.
5. What-if "+5 urgent admissions" reference optimum (brute force over all lever combinations - your CP-SAT model should reach the same objective):

| Profile | Placed | Moves | Clinician sign-offs needed | Spare beds |
|---|---|---|---|---|
| Safety-first (no assessment-required moves, no overflow) | 4 of 5 (1 transfer to another hospital) | I-03, I-06 home; P-005 step-down; A-03, A-05 direct home; overtime team; cleaned kit at 15:00 | none | 0 |
| Balanced (max 1 sign-off) | 5 of 5 | as above + expedite I-02 discharge | 1 (I-02) | 0 |
| Max-capacity | 5 of 5 | + I-10 home, + P-009 step-down | 2 (P-009, I-02) | 1 |

   With P-002 readmitted (she needs a bed, her kit returns to the pool): Safety-first 5 of 6, Balanced 6 of 6, Max-capacity 6 of 6 with 1 spare bed. The binding constraint is monitoring kits, which gives a strong demo line: "one more kit equals one more patient placed tonight".

Profile weights and hard rules are in `expected_results.json → profile_definitions`. Inpatient transfer risk = min(1, mean NEWS2 last 24 h / 5).

## Notes and limits

- The reference detectors exist only to validate the data. Build your own implementation in the product; compare against these numbers in tests.
- NEWS2 SpO2 Scale 2 should only be used for patients with confirmed hypercapnic respiratory failure, decided by a qualified clinician ([RCP NEWS2 report](https://www.rcp.ac.uk/media/a4ibkkbf/news2-final-report_0_0.pdf)). The plan file records who set it.
- Optional realism check: the open MIMIC-IV Clinical Database Demo (100 patients, ODbL licence) can be used to show the code also runs on real-world-shaped data ([PhysioNet](https://physionet.org/content/mimic-iv-demo/2.2/)).
- Before the event, confirm with the organizers that pre-generated synthetic data is allowed. If not, regenerate on site from this README using the data prompt in `claude_prompts.md`.
