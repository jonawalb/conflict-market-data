#!/usr/bin/env python3
"""Bundle a month of increments into one archive for a GitHub Release.

Minute-level price bars accumulate at roughly 5-10 MB/day once every tracked
market is live. Git keeps every version forever, so the repository grows
monotonically whether or not files are later deleted. Well before the ~1 GB mark,
bundle old months into Release assets, which do not count against repository
size, and drop them from the working tree.

    python3 scripts/consolidate.py --month 2026-08
    gh release create data-2026-08 dist/increments-2026-08.tar --notes "..."
    python3 scripts/consolidate.py --month 2026-08 --prune
    # then: git commit -am "chore: move 2026-08 increments to release"

`rebuild_db.py --since` accepts a partial history, so analysis still works
against whatever remains in the tree; download and untar a Release asset to
replay an archived month.
"""

import argparse
import hashlib
import sys
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INCREMENTS = REPO / "data" / "increments"
DIST = REPO / "dist"


def month_files(month: str) -> list:
    year, mon = month.split("-")
    return sorted((INCREMENTS / year / mon).glob("*.jsonl.gz"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", required=True, help="YYYY-MM")
    parser.add_argument("--prune", action="store_true",
                        help="delete the originals (only after uploading!)")
    args = parser.parse_args()

    files = month_files(args.month)
    if not files:
        print(f"no increments for {args.month}")
        return 1

    total = sum(f.stat().st_size for f in files)
    DIST.mkdir(exist_ok=True)
    archive = DIST / f"increments-{args.month}.tar"

    if args.prune:
        if not archive.exists():
            print(f"refusing to prune: {archive} does not exist. Bundle first.")
            return 1
        # Verify every file is actually inside the archive before deleting.
        with tarfile.open(archive) as tar:
            names = set(tar.getnames())
        missing = [f.name for f in files if f"{args.month}/{f.name}" not in names]
        if missing:
            print(f"refusing to prune: {len(missing)} files not in archive")
            return 1
        for f in files:
            f.unlink()
        print(f"pruned {len(files)} files ({total/1e6:.1f} MB) from the working tree")
        return 0

    with tarfile.open(archive, "w") as tar:
        for f in files:
            tar.add(f, arcname=f"{args.month}/{f.name}")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    print(f"bundled {len(files)} increments ({total/1e6:.1f} MB) -> {archive}")
    print(f"  archive size {archive.stat().st_size/1e6:.1f} MB")
    print(f"  sha256 {digest}")
    print(f"\nNext:\n  gh release create data-{args.month} {archive} "
          f"--notes 'Increments for {args.month}, sha256 {digest}'")
    print(f"  python3 scripts/consolidate.py --month {args.month} --prune")
    return 0


if __name__ == "__main__":
    sys.exit(main())
