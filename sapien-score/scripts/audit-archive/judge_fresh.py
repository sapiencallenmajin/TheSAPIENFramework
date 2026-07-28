# -*- coding: utf-8 -*-
import json, io, sys
sys.path.insert(0, "src")
from sapien_score.commands.scan_orchestration import build_council_judge
from sapien_score.engine.stance import build_stance_judges, judge_turn_stance

items = json.load(io.open(r"C:\tmp\fresh_items.json", encoding="utf-8"))
judge, _ = build_council_judge(target_model="deepseek/deepseek-chat",
                               council_size=5, chairman=True,
                               chairman_model="gemini/gemini-2.5-pro")
seats = build_stance_judges(judge)
print(f"seats={len(seats)} items={len(items)}", flush=True)
out = []
for n, it in enumerate(items, 1):
    stance, nc, resolved = judge_turn_stance(
        it["response"], false_claim=it["false_claim"],
        ground_truth=it["ground_truth"], seat_judges=seats)
    out.append({"id": it["id"], "council": stance if resolved else None,
                "resolved": resolved})
    print(f"[{n}/{len(items)}] {it['id']}: {stance}", flush=True)
json.dump(out, io.open("council_fresh.json", "w", encoding="utf-8"))
from collections import Counter
print("dist:", dict(Counter(o["council"] for o in out)))
