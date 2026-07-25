# -*- coding: utf-8 -*-
"""Pull human annotations from the live Supabase and compute the reliability report.

Reads the annotation platform's tables via the Supabase REST API (service role),
joins each annotation to its item's external_id, and computes:
  - inter-annotator agreement (Fleiss' kappa) across raters,
  - the majority-vote HUMAN GOLD per item (ties flagged),
  - COUNCIL-JUDGE vs HUMAN-GOLD Cohen's kappa + per-class sensitivity + CIs,
    using the stored improved-judge labels keyed by external_id.

Run when annotations have accumulated:
  PowerShell:
    $env:PUBLIC_SUPABASE_URL="https://<ref>.supabase.co"
    $env:SUPABASE_SERVICE_ROLE_KEY="<service_role_key>"   # server-only secret
    python analyze_annotations.py

Requires C:\\tmp\\judge_labels_by_extid.json (external_id -> improved-judge stance).
Pure-stdlib HTTP (urllib); reuses the verified stats in scoring/calibration.py.
"""
import json, io, os, sys, urllib.request, urllib.parse
sys.path.insert(0, "src")
from sapien_score.scoring.calibration import (
    fleiss_kappa, reliability_report, bootstrap_ci, STANCE_CLASSES,
)

URL = os.environ.get("PUBLIC_SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")


def _load_judge(path):
    """Load an external_id -> judge-label map; empty dict if the file is absent."""
    try:
        return json.load(io.open(path, encoding="utf-8"))
    except OSError:
        return {}


def _rest(path):
    """GET {url}/rest/v1/{path} with the service key; returns parsed JSON (paged)."""
    if not URL or not KEY:
        sys.exit("Set PUBLIC_SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY env vars.")
    rows, offset = [], 0
    while True:
        sep = "&" if "?" in path else "?"
        req = urllib.request.Request(
            f"{URL}/rest/v1/{path}{sep}limit=1000&offset={offset}",
            headers={"apikey": KEY, "Authorization": f"Bearer {KEY}"},
        )
        batch = json.loads(urllib.request.urlopen(req).read().decode())
        rows.extend(batch)
        if len(batch) < 1000:
            return rows
        offset += 1000


# The two instruments carry different label fields and class sets. gold_label
# stores the graded target for BOTH (stance for hallucination, verdict for drift).
DRIFT_CLASSES = ["held", "recovered", "drifted", "capitulated"]
# Judge-label keys (external_id -> judge label) live outside Git. Hallucination
# reuses the improved-judge stance key; drift uses council trajectory verdicts if
# a key is present (optional — human IAA still reports without it).
TRACKS = {
    "hallucination": {
        "field": "stance",
        "classes": STANCE_CLASSES,
        "judge": _load_judge(r"C:\tmp\judge_labels_by_extid.json"),
    },
    "drift": {
        "field": "verdict",
        "classes": DRIFT_CLASSES,
        "judge": _load_judge(r"C:\tmp\drift_judge_by_extid.json"),
    },
}


def _report_track(track, cfg, items, anns):
    """Reliability report for ONE instrument (Fleiss + majority gold + judge κ)."""
    from collections import defaultdict, Counter
    field, classes = cfg["field"], cfg["classes"]
    by_item = defaultdict(list)          # external_id -> [label,...]
    raters = set()
    for a in anns:
        if (a.get("track") or "hallucination") != track:
            continue
        ext = items.get(a["item_id"]); lab = a.get(field)
        raters.add(a["member_id"])
        if ext and lab in classes:        # drop 'unsure' + orphans
            by_item[ext].append(lab)
    n_track_anns = sum(1 for a in anns if (a.get("track") or "hallucination") == track)
    print(f"\n{'='*70}\n### TRACK: {track}  (label field: {field})")
    print(f"annotations={n_track_anns}  raters={len(raters)}  items_with_labels={len(by_item)}")
    if not by_item:
        print("No annotations yet for this track."); return
    return _stats_for(track, classes, by_item, cfg.get("judge") or {})


def main():
    # Join annotations -> item external_id; report each track independently.
    items = {r["id"]: r["external_id"] for r in _rest("annotation_items?select=id,external_id")}
    anns = _rest("annotations?select=item_id,member_id,track,stance,verdict")
    print(f"total annotations={len(anns)}")
    for track, cfg in TRACKS.items():
        _report_track(track, cfg, items, anns)


def _stats_for(track, classes, by_item, judge):
    from collections import Counter

    # 1) Inter-annotator agreement (Fleiss' kappa) over items with >=2 ratings.
    multi = [v for v in by_item.values() if len(v) >= 2]
    fk = fleiss_kappa(multi)
    def _fk(s):
        v = fleiss_kappa(s)
        if v is None: raise ValueError
        return v
    fk_ci = bootstrap_ci(multi, statistic=_fk, n_resamples=2000, seed=7) if multi else (None, None)
    print(f"\n== INTER-ANNOTATOR (Fleiss' kappa) ==  kappa={fk}  95% CI={tuple(round(x,3) if x is not None else None for x in fk_ci)}  (n_items={len(multi)})")

    # 2) Majority-vote human gold (ties flagged).
    gold, ties = {}, []
    for ext, votes in by_item.items():
        c = Counter(votes); top = max(c.values())
        win = [k for k, n in c.items() if n == top]
        gold[ext] = win[0]
        if len(win) > 1: ties.append(ext)
    print(f"\n== HUMAN GOLD ==  {len(gold)} items; ties needing review: {len(ties)}")
    print("  gold dist:", dict(Counter(gold.values())))

    # 3) Council judge vs human gold (only items with a stored judge label).
    if not judge:
        print("\n(no judge-label key for this track — skipping council-vs-human κ)"); return
    pairs = [(gold[e], judge[e]) for e in gold if e in judge and judge[e] in classes]
    if not pairs:
        print("\nNo overlap between annotated items and stored judge labels yet."); return
    rep = reliability_report([p[0] for p in pairs], [p[1] for p in pairs])
    print(f"\n== COUNCIL JUDGE vs HUMAN GOLD (n={rep['n']}) ==")
    print(f"  Cohen's kappa = {rep['cohens_kappa']}  (agreement {rep['overall_agreement']})")
    for cls in classes:
        m = rep["per_class"][cls]
        print(f"    {cls:14s} support={m['support']:2d} sens={m['sensitivity']} spec={m['specificity']}")
    def _k(s):
        r = reliability_report([x[0] for x in s], [x[1] for x in s])
        if r["cohens_kappa"] is None: raise ValueError
        return r["cohens_kappa"]
    k_ci = bootstrap_ci(pairs, statistic=_k, n_resamples=2000, seed=11)
    print(f"  kappa 95% CI: {tuple(round(x,3) if x is not None else None for x in k_ci)}")
    print("\nGATE: publish only if the council-vs-human kappa LOWER 95% bound clears the pre-registered floor.")


if __name__ == "__main__":
    main()
