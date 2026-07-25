# -*- coding: utf-8 -*-
"""Independent adversarial recompute of the calibration claims from the packet."""
import json, math
from collections import Counter, defaultdict

d = json.load(open("calib_review_packet.json", encoding="utf-8"))
N = len(d)
print(f"=== PACKET: {N} rows ===")

def confmat(gold, pred, classes):
    cm = {g: {p: 0 for p in classes} for g in classes}
    for g, p in zip(gold, pred):
        cm[g][p] += 1
    return cm

def cohens_kappa(gold, pred, classes):
    n = len(gold)
    if n == 0: return None
    cm = confmat(gold, pred, classes)
    po = sum(cm[c][c] for c in classes) / n
    gcount = Counter(gold); pcount = Counter(pred)
    pe = sum((gcount[c]/n)*(pcount[c]/n) for c in classes)
    if pe == 1: return 1.0 if po == 1 else 0.0
    return (po - pe) / (1 - pe)

def agreement(gold, pred):
    n = len(gold)
    if n == 0: return None
    return sum(1 for g, p in zip(gold, pred) if g == p) / n

def sensitivity(gold, pred, cls):
    supp = sum(1 for g in gold if g == cls)
    if supp == 0: return None, 0
    tp = sum(1 for g, p in zip(gold, pred) if g == cls and p == cls)
    return tp / supp, supp

def wilson(k, n, z=1.96):
    if n == 0: return (None, None)
    ph = k
    z2 = z*z
    denom = 1 + z2/n
    center = (ph + z2/(2*n)) / denom
    half = (z/denom) * math.sqrt(ph*(1-ph)/n + z2/(4*n*n))
    return (max(0, center-half), min(1, center+half))

CLASSES = ["retracts", "asserts_false", "evades"]

gold_all = [r["claude_gold"] for r in d]
pred_all = [r["council_label"] for r in d]

print("\n=== DISTRIBUTIONS ===")
print("claude_gold:", dict(Counter(gold_all)))
print("council_label:", dict(Counter(pred_all)))
print("gold_source:", dict(Counter(r["gold_source"] for r in d)))
print("set:", dict(Counter(r["set"] for r in d)))
print("set x gold_source:")
c = Counter((r["set"], r["gold_source"]) for r in d)
for k, v in sorted(c.items()): print("  ", k, v)

print("\n=== CLAIM 1: overall kappa & sensitivity ===")
k3 = cohens_kappa(gold_all, pred_all, CLASSES)
print(f"3-class kappa (all 116): {k3:.4f}   [claimed 0.73]")
print(f"agreement (all 116): {agreement(gold_all, pred_all):.4f}")

# binary: retracts vs not-retracts (asserts_false + evades merged as "falsehood standing")
def binarize(label, retract_positive=True):
    # retract-vs-falsehood-standing: retracts is one class, asserts_false+evades the other
    return "retracts" if label == "retracts" else "standing"
g_bin = [binarize(g) for g in gold_all]
p_bin = [binarize(p) for p in pred_all]
kb = cohens_kappa(g_bin, p_bin, ["retracts", "standing"])
print(f"binary kappa retracts-vs-standing (all 116): {kb:.4f}   [claimed 0.79]")

# asserts_false sensitivity
s_af, n_af = sensitivity(gold_all, pred_all, "asserts_false")
ci_af = wilson(s_af, n_af)
print(f"asserts_false sensitivity: {s_af:.4f}  n={n_af}  Wilson95% CI=({ci_af[0]:.3f},{ci_af[1]:.3f})   [claimed 0.875]")

s_ev, n_ev = sensitivity(gold_all, pred_all, "evades")
ci_ev = wilson(s_ev, n_ev)
print(f"evades sensitivity: {s_ev}  n={n_ev}  Wilson95% CI=({ci_ev[0]},{ci_ev[1]})   [claimed 0.0]")

s_rt, n_rt = sensitivity(gold_all, pred_all, "retracts")
ci_rt = wilson(s_rt, n_rt)
print(f"retracts sensitivity: {s_rt:.4f}  n={n_rt}  Wilson95% CI=({ci_rt[0]:.3f},{ci_rt[1]:.3f})")

print("\n=== CLAIM 2: tuned_48 vs fresh_68 split ===")
for split in ["tuned_48", "fresh_68"]:
    sub = [r for r in d if r["set"] == split]
    g = [r["claude_gold"] for r in sub]; p = [r["council_label"] for r in sub]
    k3s = cohens_kappa(g, p, CLASSES)
    gbs = [binarize(x) for x in g]; pbs = [binarize(x) for x in p]
    kbs = cohens_kappa(gbs, pbs, ["retracts", "standing"])
    ag = agreement(g, p)
    sa, na = sensitivity(g, p, "asserts_false")
    se, ne = sensitivity(g, p, "evades")
    sr, nr = sensitivity(g, p, "retracts")
    print(f"\n--- {split} (n={len(sub)}) ---")
    print(f"  gold dist: {dict(Counter(g))}")
    print(f"  pred dist: {dict(Counter(p))}")
    print(f"  agreement: {ag:.4f}")
    print(f"  3-class kappa: {k3s:.4f}")
    print(f"  binary kappa: {kbs:.4f}")
    print(f"  asserts_false sens: {sa} (n={na})")
    print(f"  evades sens: {se} (n={ne})")
    print(f"  retracts sens: {sr} (n={nr})")
    if sa is not None:
        print(f"  asserts_false Wilson CI: {wilson(sa, na)}")
    if se is not None:
        print(f"  evades Wilson CI: {wilson(se, ne)}")

print("\n=== CLAIM 2: '96% agreement on fresh_68' ===")
sub = [r for r in d if r["set"] == "fresh_68"]
g = [r["claude_gold"] for r in sub]; p = [r["council_label"] for r in sub]
print(f"fresh_68 agreement: {agreement(g,p):.4f}  [claimed 0.96]  -> {sum(1 for a,b in zip(g,p) if a==b)}/{len(g)} match")

print("\n=== THREAT (a): circular gold ===")
nclaude = sum(1 for r in d if r["gold_source"]=="claude_labeled")
nhuman = sum(1 for r in d if r["gold_source"]=="human_adjudicated")
print(f"claude_labeled: {nclaude}/{N} = {nclaude/N:.1%}")
print(f"human_adjudicated: {nhuman}/{N} = {nhuman/N:.1%}")
# kappa on claude-only subset
sub = [r for r in d if r["gold_source"]=="claude_labeled"]
g=[r["claude_gold"] for r in sub]; p=[r["council_label"] for r in sub]
print(f"claude-labeled subset kappa3: {cohens_kappa(g,p,CLASSES):.4f}  agreement={agreement(g,p):.4f} n={len(sub)}")
gb=[binarize(x) for x in g]; pb=[binarize(x) for x in p]
print(f"claude-labeled subset kappa_bin: {cohens_kappa(gb,pb,['retracts','standing']):.4f}")
# human subset
sub=[r for r in d if r["gold_source"]=="human_adjudicated"]
g=[r["claude_gold"] for r in sub]; p=[r["council_label"] for r in sub]
print(f"human-adjudicated subset kappa3: {cohens_kappa(g,p,CLASSES):.4f}  agreement={agreement(g,p):.4f} n={len(sub)}")
print(f"human-adjudicated: {sum(1 for a,b in zip(g,p) if a==b)}/{len(sub)} match -> disagreement {sum(1 for a,b in zip(g,p) if a!=b)}/{len(sub)}")
print("human-adjudicated rows gold vs council:")
for r in sub:
    flag = "  MISMATCH" if r["claude_gold"]!=r["council_label"] else ""
    print(f"  {r['id']}: gold={r['claude_gold']} council={r['council_label']}{flag}")

print("\n=== THREAT (c): 3-class evades sensitivity ===")
print(f"evades gold count: {n_ev}")
se, ne = sensitivity(gold_all, pred_all, "evades")
print(f"3-class evades sensitivity = {se}  (n={ne})  -> reproduces 0.0 claim: {se==0.0}")
# confusion for evades
cm = confmat(gold_all, pred_all, CLASSES)
print("confusion matrix rows=gold cols=council:")
print("            ", "  ".join(CLASSES))
for g_ in CLASSES:
    print(f"  {g_:12s}", "  ".join(str(cm[g_][p_]) for p_ in CLASSES))

print("\n=== THREAT (d): directional 'conservative' claim ===")
# Mislabeling an evasive non-retraction as retracts: does it understate persistence?
# Evades gold = 6. Council said retracts for how many of them?
ev_gold = [r for r in d if r["claude_gold"]=="evades"]
print(f"evades gold rows ({len(ev_gold)}):")
for r in ev_gold:
    print(f"  {r['id']} set={r['set']} council={r['council_label']} src={r['gold_source']}")
ev_to_retract = sum(1 for r in ev_gold if r["council_label"]=="retracts")
ev_to_asserts = sum(1 for r in ev_gold if r["council_label"]=="asserts_false")
ev_correct = sum(1 for r in ev_gold if r["council_label"]=="evades")
print(f"evades->retracts: {ev_to_retract}; evades->asserts_false: {ev_to_asserts}; evades->evades: {ev_correct}")
# And the reverse: retracts gold mislabeled as evades?
rt_gold = [r for r in d if r["claude_gold"]=="retracts"]
rt_to_evades = sum(1 for r in rt_gold if r["council_label"]=="evades")
print(f"retracts->evades: {rt_to_evades} (these would OVERSTATE persistence / understate retraction)")
