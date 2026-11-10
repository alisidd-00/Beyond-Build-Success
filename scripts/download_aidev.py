"""Download the pinned AIDev-pop snapshot used by Ghaleb (MSR 2026).

The base paper reports using the "Oct 28, 2025 update" of AIDev-pop (33,596 PRs).
That corresponds to Hugging Face commit 4c65121f (2025-10-28); the AIDev-pop
tables at this commit are byte-identical to the later `v3` tag. `main` has
since moved to v5 (7.6M PRs), so the revision must stay pinned.

Each file is verified against its Git LFS sha256 before it is accepted.

Usage:  uv run python scripts/download_aidev.py [--tables pull_request ...]
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import requests
from tqdm import tqdm

REPO = "hao-li/AIDev"
REVISION = "4c65121fd48b495017d725014fe0e4cdb4b599e5"  # 2025-10-28 "Update dataset"
OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "raw" / "aidev"

# Tables we need: the two Ghaleb used, plus review/timeline data for the
# human-oversight extension and repository metadata for holdouts.
DEFAULT_TABLES = [
    "pull_request",
    "pr_commit_details",
    "pr_commits",
    "pr_reviews",
    "pr_review_comments",
    "pr_comments",
    "pr_timeline",
    "repository",
    "pr_task_type",
]


def lfs_index() -> dict[str, dict]:
    url = f"https://huggingface.co/api/datasets/{REPO}/tree/{REVISION}"
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return {e["path"]: e for e in resp.json() if e["path"].endswith(".parquet")}


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(name: str, meta: dict) -> None:
    dest = OUT_DIR / name
    expected = meta["lfs"]["oid"]
    if dest.exists() and sha256_of(dest) == expected:
        print(f"ok (cached)  {name}")
        return
    url = f"https://huggingface.co/datasets/{REPO}/resolve/{REVISION}/{name}"
    tmp = dest.with_suffix(".part")
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        with tmp.open("wb") as f, tqdm(total=total, unit="B", unit_scale=True, desc=name) as bar:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
                bar.update(len(chunk))
    actual = sha256_of(tmp)
    if actual != expected:
        tmp.unlink()
        sys.exit(f"checksum mismatch for {name}: {actual} != {expected}")
    tmp.rename(dest)
    print(f"ok (verified) {name}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", nargs="*", default=DEFAULT_TABLES)
    args = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    index = lfs_index()
    for t in args.tables:
        name = f"{t}.parquet"
        if name not in index:
            sys.exit(f"{name} not found at revision {REVISION}")
        download(name, index[name])
    (OUT_DIR / "REVISION").write_text(REVISION + "\n")


if __name__ == "__main__":
    main()
