"""
Sjukhus Hemma Copilot - synthetic data pack generator (data only, no product code).

Fictional ward modelled on public facts about Skane University Hospital's
"Sjukhus hemma" (hospital-at-home) service. All patients, names and notes are
synthetic. Seeded -> fully reproducible.

Run:  python generate_datapack.py   (needs numpy + pandas)
Outputs CSV/JSON/JSONL files in ./data and expected_results.json for acceptance tests.
"""
import json, itertools, os
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

RNG = np.random.default_rng(42)
T0 = datetime(2026, 11, 21, 9, 0)          # demo clock "now" = morning round
H_START = T0 - timedelta(hours=72)         # history window start
H_END = T0 + timedelta(hours=12)           # stream end (21:00)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
os.makedirs(OUT, exist_ok=True)
iso = lambda d: d.strftime("%Y-%m-%dT%H:%M")

# --------------------------------------------------------------------------------------
# NEWS2 (Royal College of Physicians 2017) - used here ONLY to validate the dataset
# --------------------------------------------------------------------------------------
def news2(rr, spo2, on_o2, sbp, hr, cons, temp, scale=1):
    s = {}
    s["rr"] = 3 if rr <= 8 else 1 if rr <= 11 else 0 if rr <= 20 else 2 if rr <= 24 else 3
    if scale == 1:
        s["spo2"] = 3 if spo2 <= 91 else 2 if spo2 <= 93 else 1 if spo2 <= 95 else 0
    else:
        if spo2 <= 83: s["spo2"] = 3
        elif spo2 <= 85: s["spo2"] = 2
        elif spo2 <= 87: s["spo2"] = 1
        elif spo2 <= 92 or not on_o2: s["spo2"] = 0
        elif spo2 <= 94: s["spo2"] = 1
        elif spo2 <= 96: s["spo2"] = 2
        else: s["spo2"] = 3
    s["o2"] = 2 if on_o2 else 0
    s["sbp"] = 3 if sbp <= 90 else 2 if sbp <= 100 else 1 if sbp <= 110 else 0 if sbp <= 219 else 3
    s["hr"] = 3 if hr <= 40 else 1 if hr <= 50 else 0 if hr <= 90 else 1 if hr <= 110 else 2 if hr <= 130 else 3
    s["cons"] = 0 if cons == "A" else 3
    s["temp"] = 3 if temp <= 35.0 else 1 if temp <= 36.0 else 0 if temp <= 38.0 else 1 if temp <= 39.0 else 2
    return sum(s.values()), max(s.values()), s

# --------------------------------------------------------------------------------------
# Patients
# --------------------------------------------------------------------------------------
# fields: id, name, age, sex, loc, slot, admitted_h (hours rel T0), vw_start_h, dx, icd10,
# comorb, municipality, lives_alone, caregiver, consent, scale, therapy, visits, kit,
# hah_status (for inpatients), status_reason
VW = [
 ("P-001","Karin Holm",67,"F","VW-01",-120,-48,"Acute pyelonephritis","N10","Hypertension","Lund",False,"Partner","yes",1,"IV ceftriaxone 1x daily (nurse visit)",2,"K-01"),
 ("P-002","Margareta Lind",78,"F","VW-02",-72,-48,"Community-acquired pneumonia, right lower lobe","J18.9","Hypertension; Type 2 diabetes; Previous pneumonia Feb 2025 needing 2 L/min O2","Lomma",False,"Husband","yes",1,"IV cefotaxime via portable elastomeric pump",2,"K-07"),
 ("P-003","Lars Ek",72,"M","VW-03",-110,-60,"Acute decompensated heart failure","I50.0","Ischaemic heart disease; CKD stage 3a","Staffanstorp",False,"Wife","yes",1,"IV furosemide twice daily",2,"K-03"),
 ("P-004","Anders Berg",81,"M","VW-04",-96,-40,"Cellulitis, left lower leg","L03.1","Type 2 diabetes; Venous insufficiency","Eslov",False,"Wife","yes",1,"IV cloxacillin via portable pump",2,"K-04"),
 ("P-005","Eva Nystrom",59,"F","VW-05",-140,-90,"Community-acquired pneumonia (improving)","J18.9","Asthma (mild)","Lund",False,"Partner","yes",1,"Oral amoxicillin since yesterday",1,"K-05"),
 ("P-006","Johan Sjoberg",64,"M","VW-06",-100,-66,"Pulmonary embolism, low risk","I26.9","Obesity","Burlov",False,"Wife","yes",1,"Subcutaneous LMWH, oral apixaban start planned",1,"K-06"),
 ("P-007","Bengt Larsson",71,"M","VW-07",-90,-44,"COPD exacerbation with chronic hypercapnic respiratory failure","J44.1","COPD GOLD 3; Ex-smoker","Kavlinge",False,"Wife","yes",2,"Oral prednisolone; nebulisers; no home oxygen",2,"K-08"),
 ("P-008","Ingrid Wallin",85,"F","VW-08",-80,-36,"Urinary tract infection with dehydration","N39.0","Mild cognitive impairment; Osteoporosis","Malmo",False,"Daughter (daily)","yes",1,"IV fluids + IV cefuroxime",2,"K-09"),
 ("P-009","Gunnar Aberg",83,"M","VW-09",-150,-100,"Community-acquired pneumonia (improving)","J18.9","Atrial fibrillation (rate-controlled); Fall in 2024","Hoor",True,"None","yes",1,"Oral antibiotics since 36 h",2,"K-10"),
 ("P-010","Sofia Karlsson",45,"F","VW-10",-70,-30,"Acute pyelonephritis","N10","None","Malmo",False,"Partner","yes",1,"IV ceftriaxone 1x daily",2,"K-11"),
 ("P-011","Per Olsson",69,"M","VW-11",-84,-50,"Cellulitis, right forearm","L03.1","Psoriasis","Lund",False,"Wife","yes",1,"IV cloxacillin via portable pump",2,"K-12"),
 ("P-012","Birgitta Persson",76,"F","VW-12",-100,-56,"Heart failure exacerbation","I50.0","Permanent atrial fibrillation (resting HR 95-108); Hypertension","Lomma",False,"Husband","yes",1,"IV furosemide; rate control",2,"K-13"),
 ("P-013","Erik Johansson",58,"M","VW-13",-160,-120,"Diabetic foot osteomyelitis","M86.1","Type 2 diabetes; Peripheral arterial disease","Horby",False,"Wife","yes",1,"IV antibiotics, long course",2,"K-14"),
 ("P-014","Maria Andersson",62,"F","VW-14",-60,-24,"Community-acquired pneumonia","J18.9","Rheumatoid arthritis (methotrexate)","Kavlinge",False,"Partner","yes",1,"IV benzylpenicillin via pump",2,"K-15"),
]
IP = [
 ("I-01","Olof Nilsson",74,"M","BED-01",-30,"NSTEMI awaiting coronary angiography","I21.4","Type 2 diabetes","Lund",False,"Wife","n/a",1,"Telemetry; heparin",2,"not_suitable","Needs telemetry and invasive procedure"),
 ("I-02","Anna Svensson",66,"F","BED-02",-60,"Upper GI bleed (stable after endoscopy)","K92.2","NSAID use","Malmo",False,"Husband","n/a",1,"Oral PPI",0,"discharge_planned","Planned discharge 16:00 pending repeat Hb"),
 ("I-03","Kerstin Mattsson",70,"F","BED-03",-54,"Cellulitis, right lower leg","L03.1","Hypertension","Lund",False,"Husband","yes",1,"IV cloxacillin",2,"suitable","Stable >24 h, IV antibiotics deliverable via pump"),
 ("I-04","Rolf Gustafsson",77,"M","BED-04",-44,"COPD exacerbation (hypercapnic, SpO2 target 88-92)","J44.1","COPD GOLD 3","Staffanstorp",False,"Wife","yes",2,"Oral prednisolone; nebulisers",2,"needs_assessment","Intermittent RR 23 in last 24 h; physician must confirm"),
 ("I-05","Elsa Lundgren",88,"F","BED-05",-40,"Delirium secondary to UTI","F05.0","Dementia (moderate)","Lund",True,"None","n/a",1,"IV antibiotics; delirium care",2,"not_suitable","New confusion (CVPU), lives alone"),
 ("I-06","Ahmed Hassan",52,"M","BED-06",-34,"Acute pyelonephritis","N10","None","Malmo",False,"Wife","yes",1,"IV ceftriaxone",2,"suitable","Stable >24 h, afebrile 18 h"),
 ("I-07","Lena Forsberg",69,"F","BED-07",-16,"Urosepsis (day 1)","A41.9","Type 2 diabetes","Burlov",False,"Husband","n/a",1,"IV piperacillin-tazobactam; fluids",2,"not_suitable","Unstable, NEWS2 5-6"),
 ("I-08","Stig Hedlund",79,"M","BED-08",-70,"Acute decompensated heart failure (responding)","I50.0","Ischaemic heart disease","Eslov",False,"Wife","yes",1,"Switched to oral diuretics today",1,"suitable","Responding to treatment, weight down 3.1 kg"),
 ("I-09","Hanna Berglund",61,"F","BED-09",-20,"TIA work-up","G45.9","Hypertension","Kavlinge",False,"Partner","n/a",1,"Awaiting carotid imaging",0,"not_suitable","Imaging and neuro observation pending"),
 ("I-10","Nils Engstrom",73,"M","BED-10",-96,"Community-acquired pneumonia (day 4)","J18.9","Hypertension","Lomma",False,"Wife","yes",1,"Oral switch planned tomorrow",1,"suitable","Stable 48 h, NEWS2 0-1"),
 ("I-11","Mona Sandberg",68,"F","BED-11",-50,"Cellulitis, left arm","L03.1","None","Trelleborg",False,"Husband","yes",1,"IV cloxacillin",2,"not_eligible_geography","Home municipality outside Sjukhus hemma catchment"),
 ("I-12","Karl Wikstrom",80,"M","BED-12",-66,"Community-acquired pneumonia (day 3)","J18.9","Prostate hyperplasia","Kavlinge",False,"Wife","unknown",1,"IV benzylpenicillin",2,"needs_assessment","Consent for home care not yet asked"),
]

def pat_rows():
    rows = []
    for p in VW:
        (pid,name,age,sex,slot,adm,vws,dx,icd,com,mun,alone,cg,cons,scale,ther,vis,kit) = p
        rows.append(dict(patient_id=pid,name=name,age=age,sex=sex,location="VIRTUAL_WARD",bed_or_slot=slot,
            admitted_at=iso(T0+timedelta(hours=adm)),virtual_ward_start=iso(T0+timedelta(hours=vws)),
            primary_diagnosis=dx,icd10=icd,comorbidities=com,municipality=mun,lives_alone=alone,caregiver_at_home=cg,
            consent_hospital_at_home=cons,spo2_scale=scale,current_therapy=ther,visits_per_day=vis,kit_id=kit,
            hah_status="in_virtual_ward",status_reason="",planned_discharge_at=""))
    for p in IP:
        (pid,name,age,sex,slot,adm,dx,icd,com,mun,alone,cg,cons,scale,ther,vis,st,why) = p
        rows.append(dict(patient_id=pid,name=name,age=age,sex=sex,location="INPATIENT",bed_or_slot=slot,
            admitted_at=iso(T0+timedelta(hours=adm)),virtual_ward_start="",primary_diagnosis=dx,icd10=icd,
            comorbidities=com,municipality=mun,lives_alone=alone,caregiver_at_home=cg,consent_hospital_at_home=cons,
            spo2_scale=scale,current_therapy=ther,visits_per_day=vis,kit_id="",hah_status=st,status_reason=why,
            planned_discharge_at=iso(T0+timedelta(hours=7)) if pid=="I-02" else ""))
    return pd.DataFrame(rows)

patients = pat_rows()

# step-down flags for VW patients (handover to primary-care mobile team)
stepdown = {"P-005":("suitable","Stable 48 h on oral antibiotics, NEWS2 0"),
            "P-009":("needs_assessment","Clinically ready but lives alone with fall history")}
patients["stepdown_status"] = patients.patient_id.map(lambda x: stepdown.get(x,("",""))[0])
patients["stepdown_reason"] = patients.patient_id.map(lambda x: stepdown.get(x,("",""))[1])

# --------------------------------------------------------------------------------------
# Vital-sign profiles (mean values); AR(1) noise + diurnal pattern
# --------------------------------------------------------------------------------------
BASE = {  # hr, spo2, rr, sbp, temp, on_o2, cons
 "P-001":(78,97,16,128,37.0,0,"A"), "P-002":(80,96,16,132,37.1,0,"A"), "P-003":(74,95,17,118,36.8,0,"A"),
 "P-004":(82,96,16,142,37.2,0,"A"), "P-005":(72,97,15,124,36.8,0,"A"), "P-006":(84,96,17,136,36.9,0,"A"),
 "P-007":(86,89.5,20,138,36.9,0,"A"), "P-008":(80,96,16,122,37.0,0,"A"), "P-009":(76,95,16,130,36.8,0,"A"),
 "P-010":(82,98,15,116,37.3,0,"A"), "P-011":(76,96,15,134,37.0,0,"A"), "P-012":(101,95,17,126,36.8,0,"A"),
 "P-013":(80,97,15,140,37.1,0,"A"), "P-014":(84,95,17,120,37.4,0,"A"),
 "I-01":(76,96,16,134,36.9,0,"A"), "I-02":(84,97,15,118,36.8,0,"A"), "I-03":(80,97,16,128,37.0,0,"A"),
 "I-04":(88,89,20,140,37.0,0,"A"), "I-05":(92,95,19,126,37.6,0,"C"), "I-06":(84,98,15,122,37.2,0,"A"),
 "I-07":(102,95,21,108,38.3,0,"A"), "I-08":(76,95,17,116,36.8,0,"A"), "I-09":(72,97,15,146,36.7,0,"A"),
 "I-10":(80,96,16,130,37.0,0,"A"), "I-11":(78,97,15,126,37.0,0,"A"), "I-12":(86,95,18,124,37.4,0,"A"),
}
P002_SCRIPT = {  # hour after T0: (temp, rr, hr, spo2, sbp)
 1:(37.2,16,82,96,133), 2:(37.4,17,84,96,131), 3:(37.7,19,86,96,130), 4:(37.9,19,89,96,129),
 5:(38.0,20,92,95,127), 6:(38.2,20,95,95,125), 7:(38.3,20,99,94,122), 8:(38.5,21,103,94,119),
 9:(38.6,22,106,93,116), 10:(38.7,23,110,92,114), 11:(38.8,24,113,92,111), 12:(38.9,25,117,91,109)}
SD = dict(hr=2.6, spo2=0.6, rr=0.8, sbp=4.0, temp=0.12)
PHI = 0.6  # AR(1) persistence

def ar1(n, sd):
    e = RNG.normal(0, sd*np.sqrt(1-PHI**2), n); x = np.zeros(n); x[0] = RNG.normal(0, sd)
    for i in range(1, n): x[i] = PHI*x[i-1] + e[i]
    return x

def ramp(h, start, dur, power=1.0):  # h = hours relative to T0
    return float(np.clip((h-start)/dur, 0, 1))**power

rows = []
for _, p in patients.iterrows():
    pid = p.patient_id
    hr0, sp0, rr0, sbp0, t0, o20, c0 = BASE[pid]
    if p.location == "VIRTUAL_WARD":
        start = max(datetime.fromisoformat(p.virtual_ward_start), H_START); step = 1; src = "wearable+tablet"
    else:
        start = max(datetime.fromisoformat(p.admitted_at), H_START); step = 4; src = "ward_obs"
        start = start.replace(minute=0) + timedelta(hours=(4 - start.hour % 4) % 4)
    ts = []; t = start
    while t <= H_END: ts.append(t); t += timedelta(hours=step)
    n = len(ts)
    nz = {k: ar1(n, v) for k, v in SD.items()}
    for i, t in enumerate(ts):
        h = (t - T0).total_seconds()/3600; hod = t.hour
        diur = np.cos((hod-4)/24*2*np.pi)   # +1 at 04:00 (night low HR), -1 at 16:00
        hr = hr0 - 3*diur + nz["hr"][i]; sp = sp0 + nz["spo2"][i]; rr = rr0 + nz["rr"][i]
        sbp = sbp0 - 4*diur + nz["sbp"][i]; tc = t0 - 0.15*diur + nz["temp"][i]
        on_o2, cons, sq = o20, c0, round(float(np.clip(RNG.normal(0.93, 0.04), 0.6, 1.0)), 2)
        # early-stay improvement for inpatients who are getting better
        if pid in ("I-03","I-06","I-08","I-10") and h < -40: hr += 6; tc += 0.4; rr += 1
        if pid == "I-04" and h > -24 and RNG.random() < 0.35: rr += 3   # intermittent RR 23
        if pid == "I-07": tc += 0.3*max(0, 1 - (h+16)/30)
        # ---- storyline: P-002 deterioration ----
        if pid == "P-002":
            # scripted infection pattern: fever + tachypnoea first, then tachycardia, hypoxaemia, BP last
            # (values fixed so the storyline is guaranteed; small jitter on HR/SBP only)
            if h >= 1:
                tc_, rr_, hr_, sp_, sbp_ = P002_SCRIPT[int(h)]
                tc, rr, sp = tc_, rr_, sp_
                hr = hr_ + RNG.integers(-1, 2); sbp = sbp_ + RNG.integers(-2, 3)
        # ---- storyline: P-011 motion artefact at 12:00 ----
        if pid == "P-011" and h == 3:
            sp = 76; hr = hr + 38; sq = 0.22
        row = dict(patient_id=pid, ts=iso(t), hr=int(round(hr)), spo2=int(np.clip(round(sp), 70, 100)),
                   rr=int(round(rr)), sbp=int(round(sbp)), temp_c=round(tc, 1), consciousness=cons,
                   on_oxygen=int(on_o2), o2_l_min=(2 if on_o2 else 0), source=src, signal_quality=sq,
                   phase="history" if t <= T0 else "stream")
        rows.append(row)
vitals = pd.DataFrame(rows)

# --------------------------------------------------------------------------------------
# Monitoring plans (clinician-defined)
# --------------------------------------------------------------------------------------
plans = []
for _, p in patients.iterrows():
    sc = int(p.spo2_scale)
    plan = dict(patient_id=p.patient_id, spo2_scale=sc, spo2_target_low=88 if sc == 2 else 94,
                spo2_target_high=92 if sc == 2 else 100, hr_low=50, hr_high=110, rr_high=22, sbp_low=100,
                temp_high=38.0, news2_urgent_threshold=5, news2_emergency_threshold=7,
                custom_rule="", set_by="Dr. A. Rosen (consultant, internal medicine)",
                set_at=iso(datetime.fromisoformat(p.virtual_ward_start or p.admitted_at) - timedelta(hours=1)))
    if p.patient_id == "P-002":
        plan["custom_rule"] = ("Escalate to on-call physician if NEWS2 >= 5, any single parameter score 3, SpO2 < 93% "
                               "on air sustained > 1 h, or new oxygen requirement. Low threshold for readmission "
                               "given oxygen need during Feb 2025 pneumonia.")
    if p.patient_id == "P-007":
        plan["custom_rule"] = ("Confirmed hypercapnic respiratory failure on blood gas (2024 and this admission). "
                               "Use NEWS2 SpO2 Scale 2, target 88-92%. Do NOT give oxygen without physician review.")
    if p.patient_id == "I-04":
        plan["custom_rule"] = "Hypercapnic COPD. NEWS2 SpO2 Scale 2, target 88-92%."
    if p.patient_id == "P-012":
        plan["hr_high"] = 125
        plan["custom_rule"] = "Permanent AF with resting HR 95-108 is baseline. Alert on HR > 125 sustained 1 h."
    plans.append(plan)
plans = pd.DataFrame(plans)

# --------------------------------------------------------------------------------------
# Clinical notes
# --------------------------------------------------------------------------------------
def N(pid, h, role, typ, text):
    return dict(patient_id=pid, ts=iso(T0+timedelta(hours=h)), author_role=role, note_type=typ, text=text)

notes = [
 N("P-002",-72,"physician","admission",
   "78-year-old woman admitted via ED with 3 days of productive cough, fever and breathlessness. CXR: right lower lobe "
   "consolidation. CRP 186. On arrival: RR 24, SpO2 93% on air, HR 104, temp 38.9, BP 124/70, alert (NEWS2 6). "
   "Started IV cefotaxime 1 g x3. History: hypertension (amlodipine), type 2 diabetes (metformin). Previous admission "
   "Feb 2025 for left lower lobe pneumonia requiring 2 L/min oxygen for 3 days. No COPD. No known drug allergies. "
   "Lives with husband in Lomma, independent in ADL. No treatment limitations."),
 N("P-002",-50,"physician","progress",
   "Clinically improving. Afebrile > 24 h, SpO2 95-96% on air, RR 16, CRP 112. Assessed suitable for Sjukhus hemma: "
   "stable > 24 h, IV cefotaxime can continue via portable elastomeric pump, husband at home, patient consents. "
   "Plan: transfer home tomorrow 09:00, bloods day 5, escalate per monitoring plan."),
 N("P-002",-48,"nurse","transfer",
   "Transferred home 09:00. Wearable patch, tablet and kit K-07 set up. Patient and husband instructed in chat, "
   "video and when to call. First home visit completed, pump connected."),
 N("P-002",-24,"nurse","home_visit",
   "Home visit 09:10: pump changed. Patient feeling better, walked to the mailbox. Eating more. Blood glucose 8.4."),
 N("P-002",-2,"nurse","home_visit",
   "Home visit 07:05: pump changed. Slept poorly because of cough, otherwise feels okay. Vitals within plan."),
 N("P-007",-90,"physician","admission",
   "71-year-old man with COPD GOLD 3 admitted with exacerbation. Arterial blood gas: pH 7.33, pCO2 7.4 kPa - hypercapnic "
   "respiratory failure, also documented 2024. Target SpO2 88-92%. Use NEWS2 SpO2 Scale 2. Prednisolone 40 mg, nebulisers."),
 N("P-007",-46,"physician","progress",
   "Stable on air with SpO2 88-91%, which is his usual range. Suitable for Sjukhus hemma. Do not give oxygen without "
   "physician review. Wife at home."),
 N("P-005",-20,"physician","progress",
   "Pneumonia resolving. Afebrile 72 h, CRP 24. Switched to oral amoxicillin yesterday. Can be handed over to the "
   "primary-care mobile team (mobil narsjukvard) when capacity allows."),
 N("P-009",-18,"physician","progress",
   "Pneumonia improving on oral antibiotics. Medically ready for step-down. Note: lives alone, fall at home in 2024. "
   "Check home support before handover to primary care."),
 N("P-011",-50,"nurse","transfer",
   "Transferred home with wearable patch. Patient has psoriasis plaques near the sensor site; adhesion has been poor before."),
 N("P-012",-56,"physician","plan",
   "Permanent AF with resting rate 95-108 is her baseline. Custom alert set: HR > 125 sustained 1 h."),
 N("P-008",-30,"nurse","home_visit",
   "Daughter visits daily. Patient oriented to person and place, mild memory impairment at baseline."),
 N("I-03",-6,"physician","progress",
   "Cellulitis receding, marked border 4 cm smaller. Afebrile 30 h. Suitable for Sjukhus hemma with IV cloxacillin pump. "
   "Patient keen to go home."),
 N("I-04",-8,"physician","progress",
   "COPD exacerbation, improving slowly. Weaned off oxygen this morning, SpO2 88-90% on air (target 88-92). Intermittent RR 23. Could be considered for home care if stable through the day."),
 N("I-08",-5,"physician","progress",
   "Heart failure responding. Weight down 3.1 kg. Switched to oral furosemide. Wife at home. Suitable for Sjukhus hemma."),
 N("I-10",-4,"physician","progress",
   "Pneumonia day 4, stable 48 h on air. Plan oral switch tomorrow. Suitable for Sjukhus hemma or discharge in 1-2 days."),
 N("I-11",-4,"nurse","nursing",
   "Patient asks about going home early. Lives in Trelleborg."),
 N("I-12",-6,"physician","progress",
   "Pneumonia day 3, stable. Could be considered for home care; patient not yet asked."),
 N("I-02",-3,"physician","progress",
   "Hb stable 112 -> 114. Discharge planned 16:00 after repeat Hb if unchanged."),
]
for pid in ["P-001","P-003","P-004","P-006","P-010","P-013","P-014"]:
    p = patients.set_index("patient_id").loc[pid]
    notes.append(N(pid,-20,"nurse","home_visit",
        f"Home visit: {p.current_therapy}. Patient comfortable, eating and drinking. Vitals within plan. No new concerns."))
notes = sorted(notes, key=lambda x: (x["patient_id"], x["ts"]))
for i, n in enumerate(notes, 1): n["note_id"] = f"N-{i:03d}"

# --------------------------------------------------------------------------------------
# Patient chat messages (tablet)
# --------------------------------------------------------------------------------------
def M(pid, h, text, sender="patient"):
    t = T0+timedelta(hours=h)
    return dict(patient_id=pid, ts=iso(t), sender=sender, text=text, phase="history" if t <= T0 else "stream")
msgs = [
 M("P-001",-20,"What time will the nurse come tomorrow?"), M("P-004",-15,"The pump is making a beeping sound, is that normal?"),
 M("P-006",-10,"Can I take a short walk outside?"), M("P-010",-8,"Can I take paracetamol for my headache?"),
 M("P-013",-6,"When do you think I can stop the IV antibiotics?"), M("P-014",-3,"I forgot to send my morning values, sorry. Sending now."),
 M("P-005",-2,"I feel much better. When can I be discharged?"), M("P-003",1,"My weight this morning was 81.2 kg, 0.6 kg less than yesterday."),
 M("P-002",3,"Feeling more out of breath than yesterday when I walk to the bathroom. A bit shivery too."),
 M("P-011",3.2,"The patch came loose a bit when I showered, I pressed it back."),
 M("P-012",4,"Is it okay to have coffee with my medicines?"),
 M("P-002",5,"My husband thinks I look pale. Should I be worried?", sender="patient (husband typing)"),
 M("P-009",6,"Who will help me with groceries when you stop coming?"),
 M("P-008",10,"Mum seems a bit more confused this evening than usual.", sender="relative (daughter)"),
 M("P-001",11,"Thank you for today!"),
]
msgs = sorted(msgs, key=lambda x: x["ts"])
for i, m in enumerate(msgs, 1): m["message_id"] = f"M-{i:03d}"

# --------------------------------------------------------------------------------------
# Resources, incoming admissions, demand forecast
# --------------------------------------------------------------------------------------
resources = {
 "as_of": iso(T0),
 "ward": "Demo Medical Ward M-Lund (fictional; parameters inspired by SUS Sjukhus hemma, Lund/Malmo)",
 "physical_beds": {"total": 12, "occupied": 12, "overflow_beds_max": 1,
                   "overflow_note": "Overflow bed (overbelaggning) is a patient-safety risk; regional target < 1 per 100 beds"},
 "virtual_ward": {"slots_total": 18, "occupied": 14, "visit_hours": "07:00-24:00 (17 h); night = phone + direct admission"},
 "monitoring_kits": {"total": 17, "in_use": 14, "available_now": 2, "in_cleaning": 1, "cleaning_ready_at": iso(T0+timedelta(hours=6))},
 "mobile_teams": [
   {"team_id": "T1", "members": ["RN", "Assistant nurse"], "shift": "07:00-15:30 + 15:00-24:00", "visit_capacity_per_day": 10, "base_zone": "Lund"},
   {"team_id": "T2", "members": ["RN", "Assistant nurse"], "shift": "07:00-15:30 + 15:00-24:00", "visit_capacity_per_day": 10, "base_zone": "Malmo/Burlov"},
   {"team_id": "T3", "members": ["RN", "Physician (part-time)"], "shift": "07:00-15:30 + 15:00-24:00", "visit_capacity_per_day": 10, "base_zone": "North (Eslov/Hoor/Horby/Kavlinge)"}],
 "visits_booked_today": int(patients.query("location=='VIRTUAL_WARD'").visits_per_day.sum()),
 "overtime": {"extra_team_available": True, "extra_visit_capacity": 5, "cost_sek": 9500},
 "primary_care_handover": {"team": "Mobil narsjukvard (primary care mobile team)", "capacity_today": 2},
 "transfer_out": {"destination": "Neighbouring hospital (fictional)", "cost_sek_per_patient": 15000, "transport_hours": 2},
 "physician_video_review_slots_today": 4,
 "catchment_municipalities": ["Burlov","Eslov","Horby","Hoor","Kavlinge","Lomma","Lund","Malmo","Staffanstorp"],
 "approx_drive_minutes_from_lund": {"Lund":10,"Lomma":20,"Staffanstorp":20,"Kavlinge":25,"Burlov":25,"Malmo":30,"Eslov":30,"Hoor":40,"Horby":45,"Trelleborg":50},
}
admissions = pd.DataFrame([
 dict(admission_id="A-01", age=63, sex="M", working_diagnosis="Urosepsis", news2_at_ed=7, eta=iso(T0+timedelta(hours=1)),
      needs_physical_bed=True, direct_hah_possible=False, municipality="Lund", consent_hospital_at_home="n/a", visits_per_day_if_home=0),
 dict(admission_id="A-02", age=70, sex="F", working_diagnosis="NSTEMI", news2_at_ed=3, eta=iso(T0+timedelta(hours=2)),
      needs_physical_bed=True, direct_hah_possible=False, municipality="Malmo", consent_hospital_at_home="n/a", visits_per_day_if_home=0),
 dict(admission_id="A-03", age=58, sex="F", working_diagnosis="Community-acquired pneumonia (CURB-65 = 1)", news2_at_ed=2, eta=iso(T0+timedelta(hours=3)),
      needs_physical_bed=False, direct_hah_possible=True, municipality="Lund", consent_hospital_at_home="yes", visits_per_day_if_home=2),
 dict(admission_id="A-04", age=75, sex="M", working_diagnosis="COPD exacerbation needing oxygen titration", news2_at_ed=5, eta=iso(T0+timedelta(hours=4)),
      needs_physical_bed=True, direct_hah_possible=False, municipality="Staffanstorp", consent_hospital_at_home="n/a", visits_per_day_if_home=0),
 dict(admission_id="A-05", age=49, sex="M", working_diagnosis="Cellulitis, forearm, needs IV antibiotics", news2_at_ed=1, eta=iso(T0+timedelta(hours=5)),
      needs_physical_bed=False, direct_hah_possible=True, municipality="Malmo", consent_hospital_at_home="yes", visits_per_day_if_home=2),
])
hours = [T0+timedelta(hours=i) for i in range(24)]
lam = np.array([0.08 + 0.12*np.exp(-((t.hour-14)%24)**2/18) for t in hours])  # afternoon ED peak
demand = pd.DataFrame(dict(hour=[iso(t) for t in hours], expected_admissions=lam.round(3),
                           cumulative_expected=lam.cumsum().round(2)))

# --------------------------------------------------------------------------------------
# Write files
# --------------------------------------------------------------------------------------
patients.to_csv(f"{OUT}/patients.csv", index=False)
vitals.to_csv(f"{OUT}/vitals.csv", index=False)
plans.to_csv(f"{OUT}/monitoring_plans.csv", index=False)
with open(f"{OUT}/notes.jsonl","w") as f:
    for n in notes: f.write(json.dumps(n, ensure_ascii=False)+"\n")
pd.DataFrame(msgs)[["message_id","patient_id","ts","sender","text","phase"]].to_csv(f"{OUT}/messages.csv", index=False)
json.dump(resources, open(f"{OUT}/resources.json","w"), indent=2, ensure_ascii=False)
admissions.to_csv(f"{OUT}/admissions_incoming.csv", index=False)
demand.to_csv(f"{OUT}/demand_forecast.csv", index=False)
pd.DataFrame(columns=["decision_id","ts","patient_id","item_type","item_id","ai_suggestion","clinician_action",
                      "clinician_role","reason","evidence_ids"]).to_csv(f"{OUT}/decisions_template.csv", index=False)

# --------------------------------------------------------------------------------------
# Validation -> expected_results.json (acceptance tests for the team)
# --------------------------------------------------------------------------------------
v = vitals.merge(plans[["patient_id","spo2_scale"]], on="patient_id")
v["ts_dt"] = pd.to_datetime(v.ts)
def score(r, scale):
    return news2(r.rr, r.spo2, bool(r.on_oxygen), r.sbp, r.hr, r.consciousness, r.temp_c, scale)
v["news2_plan"] = [score(r, r.spo2_scale)[0] for r in v.itertuples()]
v["max_single_plan"] = [score(r, r.spo2_scale)[1] for r in v.itertuples()]
v["news2_scale1"] = [score(r, 1)[0] for r in v.itertuples()]
v["max_single_scale1"] = [score(r, 1)[1] for r in v.itertuples()]

exp = {}
# P-002 timeline
p2 = v[v.patient_id=="P-002"].sort_values("ts_dt").copy()
first = lambda df, cond: (df[cond].ts.iloc[0] if cond.any() else None)
exp["P-002"] = {
  "news2_at_T0": int(p2[p2.ts_dt==T0].news2_plan.iloc[0]),
  "first_news2_ge5": first(p2, (p2.news2_plan>=5) & (p2.ts_dt>T0)),
  "first_single_param_3": first(p2, (p2.max_single_plan>=3) & (p2.ts_dt>T0)),
  "news2_by_hour_stream": dict(zip(p2[p2.ts_dt>T0].ts.str[11:], p2[p2.ts_dt>T0].news2_plan.astype(int))),
}
# reference trend detector: fixed personal baseline from first 24 h at home (skip first 2 h)
floor = dict(hr=3, rr=1.0, spo2=0.8, temp_c=0.15, sbp=5)
direction = dict(hr=1, rr=1, spo2=-1, temp_c=1, sbp=-1)
def trend(df):
    """Reference detector: personal baseline = first home readings [first+2h, first+26h);
    robust z = (x - median)/max(1.4826*MAD, floor) in the adverse direction;
    flag when >= 2 params have z >= 2.5 on 2 consecutive good-quality readings."""
    df = df[df.signal_quality >= 0.5].sort_values("ts_dt").copy()
    t1 = df.ts_dt.min()
    b = df[(df.ts_dt >= t1+timedelta(hours=2)) & (df.ts_dt < t1+timedelta(hours=26))]
    med = {k: b[k].median() for k in floor}
    rsd = {k: max(1.4826*np.median(np.abs(b[k]-b[k].median())), floor[k]) for k in floor}
    df["n_dev"] = [sum(direction[k]*(r[k]-med[k])/rsd[k] >= 2.5 for k in floor) for _, r in df.iterrows()]
    df["flag"] = (df.n_dev >= 2) & (df.n_dev.shift(1) >= 2) & (df.ts_dt >= t1+timedelta(hours=26))
    return df, med, rsd
p2, med, rsd = trend(p2)
exp["P-002"]["reference_trend_flag_first"] = first(p2, p2.flag & (p2.ts_dt>T0))
msg_t = T0+timedelta(hours=3)
p2["flag_fused"] = p2.flag | ((p2.n_dev >= 1) & (p2.ts_dt >= msg_t) & (p2.ts_dt <= msg_t+timedelta(hours=2)))
exp["P-002"]["reference_flag_with_message_fusion_first"] = first(p2, p2.flag_fused & (p2.ts_dt>T0))
exp["P-002"]["n_params_beyond_2_5_robust_z_by_hour"] = dict(zip(p2[p2.ts_dt>T0].ts.str[11:], p2[p2.ts_dt>T0].n_dev.astype(int)))
exp["P-002"]["baseline_median"] = {k: float(round(m,1)) for k, m in med.items()}
exp["P-002"]["baseline_robust_sd"] = {k: float(round(s,2)) for k, s in rsd.items()}
exp["P-002"]["patient_message_first_symptom"] = iso(T0+timedelta(hours=3))
tf, n5 = exp["P-002"]["reference_trend_flag_first"], exp["P-002"]["first_news2_ge5"]
exp["P-002"]["lead_time_hours_trend_vs_news2"] = (datetime.fromisoformat(n5)-datetime.fromisoformat(tf)).seconds/3600 if tf and n5 else None
ff = exp["P-002"]["reference_flag_with_message_fusion_first"]
exp["P-002"]["lead_time_hours_fused_vs_news2"] = (datetime.fromisoformat(n5)-datetime.fromisoformat(ff)).seconds/3600 if ff and n5 else None

# Alert burden: naive (Scale 1 for all, every reading, no artefact filter) vs plan-aware + artefact filter
vw_ids = patients.query("location=='VIRTUAL_WARD'").patient_id
vv = v[v.patient_id.isin(vw_ids)]
naive = vv[(vv.news2_scale1>=5) | (vv.max_single_scale1>=3)]
smart = vv[((vv.news2_plan>=5) | (vv.max_single_plan>=3)) & (vv.signal_quality>=0.5)]
exp["alert_burden_virtual_ward_84h"] = {
  "naive_scale1_alert_readings": int(len(naive)),
  "naive_by_patient": naive.patient_id.value_counts().to_dict(),
  "plan_aware_filtered_alert_readings": int(len(smart)),
  "plan_aware_by_patient": smart.patient_id.value_counts().to_dict(),
  "artefact_readings_excluded": vv[vv.signal_quality<0.5][["patient_id","ts","spo2","hr","signal_quality"]].to_dict("records"),
}
exp["reference_trend_flags_per_virtual_ward_patient"] = {
    pid: int(trend(v[v.patient_id==pid])[0].flag.sum()) for pid in vw_ids}
# Inpatient NEWS2 last 24 h (for eligibility)
ip = v[(v.patient_id.str.startswith("I-")) & (v.ts_dt>T0-timedelta(hours=24)) & (v.ts_dt<=T0)]
exp["inpatient_news2_last24h"] = {pid: {"max": int(g.news2_plan.max()), "mean": round(float(g.news2_plan.mean()),2)}
                                  for pid, g in ip.groupby("patient_id")}

# ---- Reference what-if solutions (brute force; your CP-SAT should reach the same objective) ----
P = patients.set_index("patient_id")
risk = {pid: round(min(1.0, d["mean"]/5), 2) for pid, d in exp["inpatient_news2_last24h"].items()}
TR = [x for x in P.index if P.loc[x,"hah_status"] in ("suitable","needs_assessment")]   # transfer candidates
SDC = [x for x in P.index if P.loc[x,"stepdown_status"] in ("suitable","needs_assessment")]
ASSESS = set([x for x in TR if P.loc[x,"hah_status"]=="needs_assessment"] +
             [x for x in SDC if P.loc[x,"stepdown_status"]=="needs_assessment"] + ["EXPEDITE_I-02"])
CORE = ["A-01","A-02","A-04"]; HAHABLE = ["A-03","A-05"]
R = resources; VIS0 = R["visits_booked_today"]; VCAP = sum(t["visit_capacity_per_day"] for t in R["mobile_teams"])
PROFILES = {
 "safety_first": dict(max_assess=0, allow_overflow=False, w_external=100, w_assess=0,  w_overflow=0,  w_overtime=5, w_kitdelay=1, w_risk=20, w_buffer=0),
 "balanced":     dict(max_assess=1, allow_overflow=True,  w_external=100, w_assess=15, w_overflow=30, w_overtime=5, w_kitdelay=1, w_risk=10, w_buffer=2),
 "max_capacity": dict(max_assess=9, allow_overflow=True,  w_external=100, w_assess=5,  w_overflow=10, w_overtime=2, w_kitdelay=1, w_risk=5,  w_buffer=15),
}
def solve(profile, p002_readmit=False):
    W = PROFILES[profile]; best = None
    core = CORE + (["P-002"] if p002_readmit else [])
    for tr in itertools.product([0,1], repeat=len(TR)):
        trs = [x for x, b in zip(TR, tr) if b]
        for sd in itertools.product([0,1], repeat=len(SDC)):
            sds = [x for x, b in zip(SDC, sd) if b]
            if len(sds) > R["primary_care_handover"]["capacity_today"]: continue
            for expd, ovf, ot, ck in itertools.product([0,1],[0,1],[0,1],[0,1]):
                if ovf and not W["allow_overflow"]: continue
                assess = [x for x in trs+sds if x in ASSESS] + (["EXPEDITE_I-02"] if expd else [])
                if len(assess) > W["max_assess"]: continue
                for hah in itertools.product(["vw","bed","external"], repeat=len(HAHABLE)):
                    beds = len(trs) + expd + ovf                      # freed beds + optional overflow bed
                    n_vw = sum(h=="vw" for h in hah)
                    bed_users = len(core) + sum(h=="bed" for h in hah)
                    placed_in_bed = min(beds, bed_users)
                    external = bed_users - placed_in_bed + sum(h=="external" for h in hah)
                    # external hah-able patients are dominated; core overflow goes to external
                    kits = R["monitoring_kits"]["available_now"] + ck + len(sds) + (1 if p002_readmit else 0)
                    if len(trs) + n_vw > kits: continue
                    visits = VIS0 - sum(int(P.loc[x,"visits_per_day"]) for x in sds) - (2 if p002_readmit else 0) \
                             + sum(int(P.loc[x,"visits_per_day"]) for x in trs) + 2*n_vw
                    vcap = VCAP + R["overtime"]["extra_visit_capacity"]*ot
                    if visits > vcap: continue
                    slots = R["virtual_ward"]["occupied"] - len(sds) - (1 if p002_readmit else 0) + len(trs) + n_vw
                    if slots > R["virtual_ward"]["slots_total"]: continue
                    ovf_used = int(ovf and placed_in_bed == beds and beds > 0 and bed_users >= beds)
                    if ovf and not ovf_used: continue
                    buffer_beds = max(0, beds - ovf - min(beds - ovf, bed_users))
                    cost = (W["w_external"]*external + W["w_assess"]*len(assess) + W["w_overflow"]*ovf_used
                            + W["w_overtime"]*ot + W["w_kitdelay"]*ck + W["w_risk"]*sum(risk[x] for x in trs)
                            - W["w_buffer"]*buffer_beds + 0.1*(len(trs)+len(sds)))
                    sol = dict(objective=round(cost,2), patients_needing_placement=len(core)+len(HAHABLE),
                               placed=len(core)+len(HAHABLE)-external, external_transfers=external,
                               transfers_to_virtual_ward=trs, stepdowns_to_primary_care=sds,
                               expedite_I02_discharge=bool(expd), overflow_bed_used=bool(ovf_used),
                               overtime_team=bool(ot), uses_kit_from_cleaning_ready_15_00=bool(ck),
                               direct_to_virtual_ward=[a for a,h in zip(HAHABLE,hah) if h=="vw"],
                               spare_beds_after_plan=buffer_beds, visits_booked=visits, visit_capacity=vcap,
                               kits_used=len(trs)+n_vw, kits_supply=kits, virtual_slots_used=slots,
                               requires_clinician_assessment=assess)
                    if best is None or sol["objective"] < best["objective"]: best = sol
    return best
exp["inpatient_transfer_risk_score"] = risk
exp["whatif_plus5_reference"] = {pr: solve(pr) for pr in PROFILES}
exp["whatif_plus5_with_P002_readmission_reference"] = {pr: solve(pr, True) for pr in PROFILES}
exp["profile_definitions"] = PROFILES
json.dump(exp, open(f"{OUT}/expected_results.json","w"), indent=2, default=str)

print("files:", sorted(os.listdir(OUT)))
print("vitals rows:", len(vitals), "| patients:", len(patients), "| notes:", len(notes), "| messages:", len(msgs))
print(json.dumps({k: exp[k] for k in ["P-002"]}, indent=1, default=str)[:2500])
print(json.dumps(exp["alert_burden_virtual_ward_84h"], indent=1, default=str)[:1500])
print(json.dumps(exp["inpatient_news2_last24h"], indent=1))
for k in ["whatif_plus5_reference","whatif_plus5_with_P002_readmission_reference"]:
    print(k); print(json.dumps(exp[k], indent=1))
