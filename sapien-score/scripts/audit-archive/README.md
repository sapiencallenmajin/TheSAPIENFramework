# Audit archive — one-off provenance scripts

Historical scratch scripts kept for methodological provenance. None are part of
the live v3 hallucination path; do not run them against current data.

- `judge_48.py`, `judge_fresh.py` — the 2026-07-17/18 48-item stance-judge audits
  (results later RETRACTED as overclaimed; see docs/module4-design/MODULE4_STATUS.md).
  They read scratch files under `C:\tmp` that no longer ship with the repo.
- `audit_recompute.py` — recomputes reliability stats from the (gitignored)
  `calib_review_packet.json` of that audit.
- `resume_luna.sh` — resume wrapper for the completed GPT-5.6 Luna delta run
  (published; the run is finished and the checkpoint file is gitignored).
