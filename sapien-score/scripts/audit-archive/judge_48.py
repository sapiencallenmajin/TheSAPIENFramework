# -*- coding: utf-8 -*-
"""Run the real council stance-judge over the 48 audit responses (full text)."""
import json, io, sys
sys.path.insert(0, "src")
from sapien_score.commands.scan_orchestration import build_council_judge
from sapien_score.engine.stance import build_stance_judges, judge_turn_stance

# Re-extract FULL responses keyed by the same ids the curated set used.
curated = json.load(io.open(r"C:\tmp\audit_curated.json", encoding="utf-8"))
want = {it["id"]: it for it in curated}

full = {}
for f, model in [("snapback_deepseek.json", "deepseek"), ("snapback_mistral7b.json", "mistral-7b")]:
    d = json.load(io.open(f, encoding="utf-8"))
    for r in d.get("results", []):
        sid = r["scenario_id"]; dom = sid.split(".")[-2]
        for i, t in enumerate(r.get("turns", [])):
            _id = f"{model}|{dom}|t{i+1}"
            if _id in want:
                full[_id] = (t.get("assistant_response") or "").strip()

judge, _council = build_council_judge(target_model="deepseek/deepseek-chat",
                                      council_size=5, chairman=True,
                                      chairman_model="gemini/gemini-2.5-pro")
seats = build_stance_judges(judge)
print(f"seats: {len(seats)}; items: {len(full)}", flush=True)

out = []
for n, (_id, it) in enumerate(want.items(), 1):
    resp = full.get(_id, "")
    stance, new_claim, resolved = judge_turn_stance(
        resp, false_claim=it["false_claim"], ground_truth=it["ground_truth"],
        seat_judges=seats)
    out.append({"id": _id, "council": stance if resolved else None,
                "resolved": resolved, "new_claim": new_claim})
    print(f"[{n}/48] {_id}: {stance} (resolved={resolved})", flush=True)

json.dump(out, io.open("council_48.json", "w", encoding="utf-8"))
from collections import Counter
print("council dist:", dict(Counter(o["council"] for o in out)))
print("done -> council_48.json")
