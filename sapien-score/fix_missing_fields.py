#!/usr/bin/env python3
"""
Bulk-fix missing v1.5 schema fields in SAPIEN scenarios.

Fixes:
  1. regulatory_mapping — adds ["NIST AI RMF MAP 2.3"] if missing
  2. pressure_type (top-level) — copies from first escalation if missing
  3. tags — adds empty list if missing

Usage:
  python fix_missing_fields.py --dry-run --domain workplace --dir src/sapien_score/scenario_data/sapien
  python fix_missing_fields.py --domain workplace --dir src/sapien_score/scenario_data/sapien
  python fix_missing_fields.py --all --dir src/sapien_score/scenario_data/sapien
"""

import json
import sys
from pathlib import Path
import argparse


# Default regulatory mapping — NIST AI RMF is the baseline
# for every scenario. Specific statutes added manually.
DEFAULT_REGULATORY_MAPPING = ["NIST AI RMF MAP 2.3"]


def process_file(filepath: Path, dry_run: bool) -> dict:
    """Fix missing fields in a single scenario file."""
    with open(filepath, "r", encoding="utf-8") as f:
        original_text = f.read()

    try:
        data = json.loads(original_text)
    except json.JSONDecodeError as e:
        return {"file": str(filepath), "status": "ERROR", "reason": f"Invalid JSON: {e}"}

    changes = []

    # Fix 1: regulatory_mapping
    if not data.get("regulatory_mapping"):
        data["regulatory_mapping"] = list(DEFAULT_REGULATORY_MAPPING)
        changes.append("+ regulatory_mapping: ['NIST AI RMF MAP 2.3']")

    # Fix 2: pressure_type (top-level)
    if not data.get("pressure_type"):
        escalations = data.get("escalations", [])
        if escalations and escalations[0].get("pressure_type"):
            pt = escalations[0]["pressure_type"]
            data["pressure_type"] = pt
            changes.append(f"+ pressure_type: '{pt}' (from first escalation)")
        else:
            data["pressure_type"] = "normalization"
            changes.append("+ pressure_type: 'normalization' (default)")

    # Fix 3: tags
    if not data.get("tags"):
        # Generate basic tags from domain and title
        domain = data.get("domain", "unknown")
        tags = [domain]
        data["tags"] = tags
        changes.append(f"+ tags: {tags}")

    if not changes:
        return {"file": str(filepath), "status": "SKIP", "reason": "all fields present"}

    result = {
        "file": str(filepath),
        "changes": changes,
    }

    if dry_run:
        result["status"] = "WOULD_FIX"
        return result

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    result["status"] = "FIXED"
    return result


def main():
    parser = argparse.ArgumentParser(description="Bulk-fix missing v1.5 schema fields")
    parser.add_argument("--dir", required=True, help="Root scenarios directory")
    parser.add_argument("--domain", help="Single domain to process")
    parser.add_argument("--all", dest="process_all", action="store_true", help="Process all domains")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    args = parser.parse_args()

    root = Path(args.dir)
    if not root.exists():
        print(f"ERROR: Directory not found: {root}")
        sys.exit(1)

    if args.domain:
        files = sorted((root / args.domain).glob("*.json"))
    elif args.process_all:
        files = sorted(root.rglob("*.json"))
    else:
        print("ERROR: Specify --domain or --all")
        sys.exit(1)

    if not files:
        print("No JSON files found.")
        sys.exit(0)

    counts = {"FIXED": 0, "WOULD_FIX": 0, "SKIP": 0, "ERROR": 0}

    if args.dry_run:
        print(f"\n{'='*60}")
        print(f"DRY RUN — no files will be modified")
        print(f"{'='*60}\n")

    for f in files:
        result = process_file(f, args.dry_run)
        status = result["status"]
        counts[status] = counts.get(status, 0) + 1

        if status in ("FIXED", "WOULD_FIX"):
            marker = "+" if status == "FIXED" else "~"
            print(f"  {marker} {result['file']}")
            for c in result["changes"]:
                print(f"      {c}")
        elif status == "ERROR":
            print(f"  ✗ {result['file']}: {result['reason']}")

    print(f"\n{'='*60}")
    print(f"Files scanned: {len(files)}")
    if args.dry_run:
        print(f"Would fix: {counts.get('WOULD_FIX', 0)}")
    else:
        print(f"Fixed: {counts.get('FIXED', 0)}")
    print(f"Already complete: {counts.get('SKIP', 0)}")
    print(f"Errors: {counts.get('ERROR', 0)}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
