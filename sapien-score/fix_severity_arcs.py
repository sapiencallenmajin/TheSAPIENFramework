#!/usr/bin/env python3
"""
Fix non-monotonic severity arcs in SAPIEN scenarios.

Problem: Old scenarios have severity 0 on rapport/context turns,
which the validator flags as non-monotonic. 

Fix rules:
  1. Any severity 0 → bump to 1 (minimum valid severity)
  2. After fixing zeros, check if arc is monotonic (±1 tolerance)
  3. If still non-monotonic, smooth by ensuring each turn is
     >= previous turn's severity - 1 (allow small dips, not big drops)

Usage:
  python fix_severity_arcs.py --dry-run --domain financial --dir src/sapien_score/scenario_data/sapien
  python fix_severity_arcs.py --domain financial --dir src/sapien_score/scenario_data/sapien
  python fix_severity_arcs.py --all --dir src/sapien_score/scenario_data/sapien
"""

import json
import sys
from pathlib import Path
import argparse


def is_monotonic(arc: list[int], tolerance: int = 1) -> bool:
    """Check if severity arc is monotonically increasing within tolerance."""
    for i in range(1, len(arc)):
        if arc[i] < arc[i-1] - tolerance:
            return False
    return True


def fix_arc(arc: list[int]) -> tuple[list[int], list[str]]:
    """Fix a severity arc. Returns (fixed_arc, list_of_changes)."""
    changes = []
    fixed = list(arc)
    
    # Step 1: Bump any 0s to 1
    for i, s in enumerate(fixed):
        if s == 0:
            fixed[i] = 1
            changes.append(f"T{i}: 0 → 1 (minimum severity)")
    
    # Step 2: Check if monotonic now
    if is_monotonic(fixed):
        return fixed, changes
    
    # Step 3: Smooth — ensure no turn drops more than 1 below previous
    for i in range(1, len(fixed)):
        if fixed[i] < fixed[i-1] - 1:
            old = fixed[i]
            fixed[i] = fixed[i-1]
            changes.append(f"T{i}: {old} → {fixed[i]} (smoothed to match previous)")
    
    # Step 4: If still not monotonic after forward pass, 
    # there might be a spike-then-drop. Do a backward pass
    # to pull early spikes down.
    if not is_monotonic(fixed):
        for i in range(len(fixed) - 2, -1, -1):
            if fixed[i] > fixed[i+1] + 1:
                old = fixed[i]
                fixed[i] = fixed[i+1]
                changes.append(f"T{i}: {old} → {fixed[i]} (backward smooth)")
    
    return fixed, changes


def process_file(filepath: Path, dry_run: bool) -> dict:
    """Fix severity arc in a single scenario file."""
    with open(filepath, "r", encoding="utf-8") as f:
        original_text = f.read()
    
    try:
        data = json.loads(original_text)
    except json.JSONDecodeError as e:
        return {"file": str(filepath), "status": "ERROR", "reason": f"Invalid JSON: {e}"}
    
    escalations = data.get("escalations", [])
    if not escalations:
        return {"file": str(filepath), "status": "SKIP", "reason": "no escalations"}
    
    # Get current arc
    current_arc = [e.get("severity", 0) for e in escalations]
    
    # Check if already valid
    if is_monotonic(current_arc) and 0 not in current_arc:
        return {"file": str(filepath), "status": "SKIP", "reason": "arc already valid"}
    
    # Fix it
    fixed_arc, changes = fix_arc(current_arc)
    
    if fixed_arc == current_arc:
        return {"file": str(filepath), "status": "SKIP", "reason": "no changes needed"}
    
    result = {
        "file": str(filepath),
        "original": current_arc,
        "fixed": fixed_arc,
        "changes": changes,
    }
    
    if dry_run:
        result["status"] = "WOULD_FIX"
        return result
    
    # Apply fixes to the actual escalation objects
    for i, new_sev in enumerate(fixed_arc):
        escalations[i]["severity"] = new_sev
    
    # Write back
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    
    result["status"] = "FIXED"
    return result


def main():
    parser = argparse.ArgumentParser(description="Fix non-monotonic severity arcs")
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
            print(f"    Before: {result['original']}")
            print(f"    After:  {result['fixed']}")
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
    print(f"Already valid: {counts.get('SKIP', 0)}")
    print(f"Errors: {counts.get('ERROR', 0)}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
