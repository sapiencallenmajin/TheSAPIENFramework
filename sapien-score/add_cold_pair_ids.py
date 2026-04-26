#!/usr/bin/env python3
"""
Bulk-add cold_pair_id to SAPIEN scenarios that are missing it.

Usage:
  python add_cold_pair_ids.py --dry-run --dir src/sapien_score/scenario_data/sapien
  python add_cold_pair_ids.py --domain financial --dir src/sapien_score/scenario_data/sapien
  python add_cold_pair_ids.py --all --dir src/sapien_score/scenario_data/sapien

--dry-run shows what would change without writing files.
Start with one domain, validate, then do --all.
"""

import json
import sys
from pathlib import Path
import argparse


def generate_cold_pair_id(scenario_id: str) -> str:
    """Generate cold_pair_id from scenario id.
    
    sapien.financial.crypto_tax_evasion.v1 
    → sapien.financial.crypto_tax_evasion_cold.v1
    """
    parts = scenario_id.rsplit(".v", 1)
    if len(parts) == 2:
        return f"{parts[0]}_cold.v{parts[1]}"
    return f"{scenario_id}_cold"


def process_file(filepath: Path, dry_run: bool) -> dict:
    """Add cold_pair_id if missing. Returns status dict."""
    with open(filepath, "r", encoding="utf-8") as f:
        original_text = f.read()
        
    try:
        data = json.loads(original_text)
    except json.JSONDecodeError as e:
        return {"file": str(filepath), "status": "ERROR", "reason": f"Invalid JSON: {e}"}
    
    # Skip if already has cold_pair_id
    if data.get("cold_pair_id"):
        return {"file": str(filepath), "status": "SKIP", "reason": "already has cold_pair_id"}
    
    # Skip if no id field
    scenario_id = data.get("id")
    if not scenario_id:
        return {"file": str(filepath), "status": "ERROR", "reason": "no id field"}
    
    # Generate and add
    cold_id = generate_cold_pair_id(scenario_id)
    data["cold_pair_id"] = cold_id
    
    if dry_run:
        return {"file": str(filepath), "status": "WOULD_ADD", "cold_pair_id": cold_id}
    
    # Write back — preserve formatting
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")  # trailing newline
    
    return {"file": str(filepath), "status": "ADDED", "cold_pair_id": cold_id}


def main():
    parser = argparse.ArgumentParser(description="Bulk-add cold_pair_id to SAPIEN scenarios")
    parser.add_argument("--dir", required=True, help="Root scenarios directory")
    parser.add_argument("--domain", help="Single domain to process")
    parser.add_argument("--all", dest="process_all", action="store_true", help="Process all domains")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    args = parser.parse_args()
    
    root = Path(args.dir)
    if not root.exists():
        print(f"ERROR: Directory not found: {root}")
        sys.exit(1)
    
    # Find files
    if args.domain:
        domain_dir = root / args.domain
        if not domain_dir.exists():
            print(f"ERROR: Domain directory not found: {domain_dir}")
            sys.exit(1)
        files = sorted(domain_dir.glob("*.json"))
    elif args.process_all:
        files = sorted(root.rglob("*.json"))
    else:
        print("ERROR: Specify --domain or --all")
        sys.exit(1)
    
    if not files:
        print("No JSON files found.")
        sys.exit(0)
    
    # Process
    results = {"ADDED": 0, "WOULD_ADD": 0, "SKIP": 0, "ERROR": 0}
    
    if args.dry_run:
        print(f"\n{'='*60}")
        print(f"DRY RUN — no files will be modified")
        print(f"{'='*60}\n")
    
    for f in files:
        result = process_file(f, args.dry_run)
        status = result["status"]
        results[status] = results.get(status, 0) + 1
        
        if status in ("ADDED", "WOULD_ADD"):
            marker = "+" if status == "ADDED" else "~"
            print(f"  {marker} {result['file']}")
            print(f"    → {result['cold_pair_id']}")
        elif status == "ERROR":
            print(f"  ✗ {result['file']}: {result['reason']}")
        # SKIP = silent
    
    # Summary
    print(f"\n{'='*60}")
    print(f"Files scanned: {len(files)}")
    if args.dry_run:
        print(f"Would add cold_pair_id: {results.get('WOULD_ADD', 0)}")
    else:
        print(f"Added cold_pair_id: {results.get('ADDED', 0)}")
    print(f"Already had cold_pair_id: {results.get('SKIP', 0)}")
    print(f"Errors: {results.get('ERROR', 0)}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
