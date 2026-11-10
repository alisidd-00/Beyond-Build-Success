"""Fetch GitHub Actions workflow files at the revisions needed for the security delta.

For every AIDev-pop PR that touches .github/workflows/*.yml (2,318 PRs):
  1. GitHub REST  GET /repos/{repo}/pulls/{n}/commits  -> ordered PR commits + parents
     (authoritative order; also recovers commits beyond AIDev's 30-per-PR truncation).
  2. For every workflow path the PR touches, plan these revisions:
       base        parent of the first PR commit (what the agent started from)
       touch_k     each PR commit that touched the path, in PR order (touch_1 = first attempt)
       final       last PR commit (head at collection time)
  3. Download file text from raw.githubusercontent.com and store it under
       data/interim/revisions/<pr_id>/<sha>/<path>
     keeping the original path so zizmor recognises the file as a workflow.

Nothing fetched is executed. Missing evidence is recorded as such (never imputed):
  fetch_status in {ok, absent (404 at that revision), pr_unavailable, error}.

Requires a token for the full run (60 req/h unauthenticated):
  export GITHUB_TOKEN=...   # fine-grained, public repositories read-only
  uv run python scripts/fetch_revisions.py [--limit N] [--workers 8]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import duckdb
import pandas as pd
import requests
from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "aidev"
INTERIM = ROOT / "data" / "interim"
CACHE = INTERIM / "gh_cache" / "pull_commits"
REV_DIR = INTERIM / "revisions"
MANIFEST = INTERIM / "revision_manifest.parquet"
PR_META = INTERIM / "cohort_pr_commits.parquet"

GHA_REGEX = r"\.github/workflows/.*\.ya?ml$"  # same as Ghaleb's rq_3.py

TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
_local = threading.local()


def session() -> requests.Session:
    if not hasattr(_local, "s"):
        s = requests.Session()
        s.headers["User-Agent"] = "cicd-rai-research"
        s.headers["Accept"] = "application/vnd.github+json"
        if TOKEN:
            s.headers["Authorization"] = f"Bearer {TOKEN}"
        _local.s = s
    return _local.s


def get(url: str, *, params=None, max_tries: int = 6) -> requests.Response:
    """GET with retries; sleeps on primary/secondary rate limits."""
    for attempt in range(max_tries):
        try:
            r = session().get(url, params=params, timeout=60)
        except requests.RequestException:
            time.sleep(2 ** attempt)
            continue
        if r.status_code in (403, 429) and (
            r.headers.get("X-RateLimit-Remaining") == "0" or "rate limit" in r.text.lower()
        ):
            reset = int(r.headers.get("X-RateLimit-Reset", time.time() + 60))
            wait = max(int(r.headers.get("Retry-After", 0)), reset - int(time.time())) + 2
            if not TOKEN and wait > 120:
                raise SystemExit(
                    f"Unauthenticated rate limit hit (resets in {wait // 60} min). "
                    "Set GITHUB_TOKEN to continue."
                )
            tqdm.write(f"rate limited; sleeping {wait}s")
            time.sleep(wait)
            continue
        if r.status_code >= 500:
            time.sleep(2 ** attempt + random.random())
            continue
        return r
    return r


def load_cohort() -> pd.DataFrame:
    con = duckdb.connect()
    return con.sql(f"""
        SELECT p.id AS pr_id, p.number, p.agent,
               replace(p.repo_url, 'https://api.github.com/repos/', '') AS repo,
               f.sha, f.filename AS path, f.status
        FROM '{RAW / 'pull_request.parquet'}' p
        JOIN '{RAW / 'pr_commit_details.parquet'}' f ON f.pr_id = p.id
        WHERE regexp_matches(f.filename, '{GHA_REGEX}', 'i')
    """).df()


def pr_commits(pr_id: int, repo: str, number: int) -> dict:
    cache = CACHE / f"{pr_id}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    commits, status = [], "ok"
    for page in range(1, 4):  # API returns at most 250 commits per PR
        r = get(f"https://api.github.com/repos/{repo}/pulls/{number}/commits",
                params={"per_page": 100, "page": page})
        if r.status_code != 200:
            status = f"http_{r.status_code}"
            break
        batch = r.json()
        commits += [{"sha": c["sha"], "parents": [p["sha"] for p in c.get("parents", [])],
                     "author_date": (c.get("commit") or {}).get("author", {}).get("date"),
                     "committer_date": (c.get("commit") or {}).get("committer", {}).get("date")}
                    for c in batch]
        if len(batch) < 100:
            break
    result = {"pr_id": pr_id, "status": status, "commits": commits}
    if status == "ok" or status in ("http_404", "http_410", "http_451"):
        cache.write_text(json.dumps(result))  # cache definitive answers only
    return result


def fetch_file(repo: str, sha: str, path: str) -> tuple[str, str | None]:
    dest = REV_DIR / "_by_sha" / repo.replace("/", "__") / sha / path
    marker = dest.with_name(dest.name + ".absent")
    if dest.exists():
        return "ok", hashlib.sha256(dest.read_bytes()).hexdigest()
    if marker.exists():
        return "absent", None
    r = get(f"https://raw.githubusercontent.com/{repo}/{sha}/{path}")
    if r.status_code == 404:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
        return "absent", None
    if r.status_code != 200:
        return f"error_{r.status_code}", None
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(r.content)
    return "ok", hashlib.sha256(r.content).hexdigest()


def plan_pr(pr: pd.DataFrame, meta: dict) -> list[dict]:
    first = pr.iloc[0]
    rows = []
    base_common = {"pr_id": int(first.pr_id), "repo": first.repo, "number": int(first.number),
                   "agent": first.agent}
    if meta["status"] != "ok" or not meta["commits"]:
        for path in pr.path.unique():
            rows.append({**base_common, "path": path, "role": "all", "order": -1, "sha": None,
                         "fetch_status": "pr_unavailable", "content_sha256": None,
                         "pr_status": meta["status"]})
        return rows
    order = [c["sha"] for c in meta["commits"]]
    pos = {s: i for i, s in enumerate(order)}
    base_sha = (meta["commits"][0]["parents"] or [None])[0]
    final_sha = order[-1]
    for path, rows_p in pr.groupby("path"):
        touch = sorted({s for s in rows_p.sha if s in pos}, key=pos.get)
        plan = [("base", -1, base_sha)]
        plan += [(f"touch_{i + 1}", pos[s], s) for i, s in enumerate(touch)]
        if final_sha not in touch:
            plan.append(("final", pos[final_sha], final_sha))
        else:
            plan[-1] = (plan[-1][0] + "+final", plan[-1][1], plan[-1][2])
        statuses = set(rows_p.status)
        for role, idx, sha in plan:
            rows.append({**base_common, "path": path, "role": role, "order": idx, "sha": sha,
                         "aidev_statuses": ",".join(sorted(statuses)),
                         "untracked_touch_shas": int(sum(s not in pos for s in set(rows_p.sha))),
                         "n_pr_commits": len(order), "pr_status": "ok"})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="only the first N PRs (smoke test)")
    ap.add_argument("--workers", type=int, default=8 if TOKEN else 2)
    args = ap.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    REV_DIR.mkdir(parents=True, exist_ok=True)

    cohort = load_cohort()
    prs = cohort[["pr_id", "repo", "number"]].drop_duplicates().sort_values("pr_id")
    if args.limit:
        prs = prs.head(args.limit)
    print(f"{len(prs)} PRs, token={'yes' if TOKEN else 'NO (60 req/h)'}")

    metas: dict[int, dict] = {}
    with ThreadPoolExecutor(args.workers) as ex:
        futs = {ex.submit(pr_commits, r.pr_id, r.repo, r.number): r.pr_id for r in prs.itertuples()}
        for f in tqdm(as_completed(futs), total=len(futs), desc="PR commits"):
            metas[futs[f]] = f.result()

    plan = []
    for pr_id, grp in cohort[cohort.pr_id.isin(metas)].groupby("pr_id"):
        plan += plan_pr(grp, metas[pr_id])
    plan = pd.DataFrame(plan)

    todo = plan[plan.sha.notna()][["repo", "sha", "path"]].drop_duplicates()
    results = {}
    with ThreadPoolExecutor(args.workers) as ex:
        futs = {ex.submit(fetch_file, r.repo, r.sha, r.path): (r.repo, r.sha, r.path)
                for r in todo.itertuples()}
        for f in tqdm(as_completed(futs), total=len(futs), desc="files"):
            results[futs[f]] = f.result()
    plan["fetch_status"] = [results.get((r.repo, r.sha, r.path), (r.fetch_status if "fetch_status" in plan
                            and isinstance(r.fetch_status, str) else "pr_unavailable", None))[0]
                            for r in plan.itertuples()]
    plan["content_sha256"] = [results.get((r.repo, r.sha, r.path), (None, None))[1] for r in plan.itertuples()]

    commits_df = pd.DataFrame([{"pr_id": k, "pr_status": v["status"], "order": i, **c}
                               for k, v in metas.items() for i, c in enumerate(v["commits"])])
    commits_df["parents"] = commits_df.get("parents", pd.Series(dtype=object)).map(
        lambda p: ",".join(p) if isinstance(p, list) else None)
    commits_df.to_parquet(PR_META, index=False)
    plan.to_parquet(MANIFEST, index=False)
    print(plan.groupby(["role"]).size().rename("n").head(10).to_string())
    print(plan.fetch_status.value_counts().to_string())
    print(f"wrote {MANIFEST.relative_to(ROOT)} ({len(plan)} rows) and {PR_META.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
