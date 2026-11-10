"""Token-free alternative to fetch_revisions.py: get PR history and workflow files with plain git.

Same outputs as fetch_revisions.py (revision_manifest.parquet, cohort_pr_commits.parquet and
data/interim/revisions/_by_sha/...), so cicd_rai.scan and cicd_rai.align run unchanged.

Per repository (640 repos, 2,318 PRs):
  1. bare partial clone, then ONE shallow, blob-less fetch of every cohort PR head
       git fetch --filter=blob:none --depth=D origin +refs/pull/<n>/head:refs/pr/<n> ...
  2. per PR: first-parent chain from the PR head, cut at the oldest commit AIDev lists for that PR
     -> ordered PR commits with true parents (base = parent of the first PR commit)
  3. resolve each needed (commit, path) to a blob id from the local trees (absent -> 'absent')
  4. ONE batched fetch of all needed blobs, then read them with cat-file

No token, no REST API quota, no stored credentials (credential helpers and askpass are disabled
so a deleted/private repo fails instead of prompting). Only public data is read; nothing is executed.
Results are cached per repo in data/interim/git_cache/, so the script can be re-run to resume.

Usage:  uv run python scripts/fetch_revisions_git.py [--limit-prs N] [--workers 8]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import duckdb
import pandas as pd
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_revisions import INTERIM, MANIFEST, PR_META, RAW, REV_DIR, load_cohort, plan_pr  # noqa: E402

GIT_DIR = INTERIM / "git"
CACHE = INTERIM / "git_cache"
MAX_DEPTH = 300

GIT_ENV = {k: v for k, v in os.environ.items()
           if not k.startswith(("GIT_ASKPASS", "SSH_ASKPASS", "VSCODE_GIT"))}
GIT_ENV.update({"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"})


def git(d: Path, *args: str, stdin: bytes | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(d), "-c", "credential.helper=", "-c", "core.askPass=", *args],
                          input=stdin, capture_output=True, env=GIT_ENV, timeout=timeout)


def ensure_repo(repo: str) -> Path:
    d = GIT_DIR / (repo.replace("/", "__") + ".git")
    if not (d / "HEAD").exists():
        d.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "--bare", str(d)], check=True, env=GIT_ENV)
        git(d, "remote", "add", "origin", f"https://github.com/{repo}.git")
        git(d, "config", "remote.origin.promisor", "true")
        git(d, "config", "remote.origin.partialclonefilter", "blob:none")
    return d


def fetch_refs(d: Path, numbers: list[int], depth: int) -> dict[int, str]:
    """Fetch PR heads; returns {number: error} for the ones that failed."""
    spec = [f"+refs/pull/{n}/head:refs/pr/{n}" for n in numbers]
    r = git(d, "fetch", "-q", "--no-tags", "--filter=blob:none", f"--depth={depth}", "origin", *spec)
    if r.returncode == 0:
        return {}
    if len(numbers) == 1:
        return {numbers[0]: r.stderr.decode(errors="replace").strip().splitlines()[-1][:200]}
    errors = {}
    for n in numbers:  # one bad ref fails the batch: retry individually
        errors.update(fetch_refs(d, [n], depth))
    return errors


def chain(d: Path, number: int, depth: int) -> list[dict]:
    r = git(d, "log", "--first-parent", f"-n{depth}", "--format=%H|%P|%aI|%cI", f"refs/pr/{number}")
    out = []
    for line in r.stdout.decode().splitlines():
        sha, parents, ad, cd = line.split("|")
        out.append({"sha": sha, "parents": parents.split(), "author_date": ad, "committer_date": cd})
    return out  # newest first


def pr_meta(d: Path, number: int, aidev_shas: list[str], depth: int) -> dict:
    known = set(aidev_shas)
    for attempt in range(2):
        c = chain(d, number, depth)
        hits = [i for i, x in enumerate(c) if x["sha"] in known]
        if not hits:
            return {"status": "history_mismatch", "commits": []}  # e.g. force-pushed after collection
        commits = list(reversed(c[: max(hits) + 1]))  # oldest first
        if commits[0]["parents"] or attempt == 1:
            break
        # first PR commit sits on the shallow boundary (parents hidden): deepen once and retry
        git(d, "fetch", "-q", "--no-tags", "--filter=blob:none", "--deepen=50", "origin",
            f"+refs/pull/{number}/head:refs/pr/{number}")
    return {"status": "ok", "commits": commits}


def resolve_blobs(d: Path, wanted: pd.DataFrame) -> dict[tuple[str, str], str | None]:
    """(sha, path) -> blob id from local trees, or None if the path does not exist at that commit."""
    out = {}
    for sha, grp in wanted.groupby("sha"):
        paths = list(grp.path.unique())
        r = git(d, "ls-tree", "-z", sha, "--", *paths)
        found = {}
        for entry in r.stdout.decode(errors="replace").split("\0"):
            if "\t" in entry:
                meta, p = entry.split("\t", 1)
                mode, typ, oid = meta.split()
                if typ == "blob":
                    found[p] = oid
        out.update({(sha, p): found.get(p) for p in paths})
    return out


def read_blobs(d: Path, oids: list[str]) -> dict[str, bytes]:
    if not oids:
        return {}
    git(d, "fetch", "-q", "--no-tags", "--no-write-fetch-head", "--filter=blob:none", "--stdin", "origin",
        stdin="\n".join(oids).encode() + b"\n")  # batched; cat-file lazily fetches anything still missing
    r = git(d, "cat-file", "--batch", stdin="\n".join(oids).encode() + b"\n")
    buf, out, pos = r.stdout, {}, 0
    while pos < len(buf):
        nl = buf.index(b"\n", pos)
        header = buf[pos:nl].decode().split()
        pos = nl + 1
        if len(header) == 3:
            size = int(header[2])
            out[header[0]] = buf[pos:pos + size]
            pos += size + 1
    return out


def process_repo(repo: str, cohort: pd.DataFrame, aidev_commits: dict[int, list[str]]) -> dict:
    cache = CACHE / (repo.replace("/", "__") + ".json")
    if cache.exists():
        cached = json.loads(cache.read_text())
        # reuse only if the cache covers every cohort PR of this repo (a --limit-prs run may not)
        if set(cohort.pr_id.astype(int)) <= set(cached.get("pr_ids", [])):
            return cached
    d = ensure_repo(repo)
    prs = cohort[["pr_id", "number"]].drop_duplicates()
    sizes = [len(aidev_commits.get(p, [])) for p in prs.pr_id]
    depth = min(MAX_DEPTH, max((s + 3 if s < 30 else MAX_DEPTH) for s in sizes) if sizes else 10)
    errors = fetch_refs(d, sorted(prs.number.astype(int).tolist()), depth)

    plan, commits = [], []
    for r in prs.itertuples():
        if int(r.number) in errors:
            meta = {"status": "fetch_failed: " + errors[int(r.number)], "commits": []}
        else:
            meta = pr_meta(d, int(r.number), aidev_commits.get(r.pr_id, []), depth)
        plan += plan_pr(cohort[cohort.pr_id == r.pr_id], meta)
        commits += [{"pr_id": int(r.pr_id), "pr_status": meta["status"], "order": i, **c,
                     "parents": ",".join(c["parents"])} for i, c in enumerate(meta["commits"])]

    plan_df = pd.DataFrame(plan)
    if "sha" in plan_df and plan_df.sha.notna().any():
        wanted = plan_df[plan_df.sha.notna()][["sha", "path"]].drop_duplicates()
        oid_of = resolve_blobs(d, wanted)
        blobs = read_blobs(d, sorted({o for o in oid_of.values() if o}))
        for (sha, path), oid in oid_of.items():
            if oid and oid in blobs:
                dest = REV_DIR / "_by_sha" / repo.replace("/", "__") / sha / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(blobs[oid])
        status = {k: ("absent" if o is None else "ok" if o in blobs else "error_blob") for k, o in oid_of.items()}
        digest = {k: hashlib.sha256(blobs[o]).hexdigest() for k, o in oid_of.items() if o and o in blobs}
        plan_df["fetch_status"] = [status.get((s, p)) if isinstance(s, str) else
                                   ("pr_unavailable" if ps != "ok" else "no_sha")
                                   for s, p, ps in zip(plan_df.sha, plan_df.path, plan_df.pr_status)]
        plan_df["content_sha256"] = [digest.get((s, p)) for s, p in zip(plan_df.sha, plan_df.path)]
    result = {"repo": repo, "pr_ids": sorted(int(p) for p in prs.pr_id),
              "plan": plan_df.to_dict("records"), "commits": commits}
    CACHE.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result, default=str))
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-prs", type=int, default=None, help="only the first N PRs (by id), for testing")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    for p in (GIT_DIR, CACHE, REV_DIR):
        p.mkdir(parents=True, exist_ok=True)

    cohort = load_cohort()
    if args.limit_prs:
        keep = sorted(cohort.pr_id.unique())[: args.limit_prs]
        cohort = cohort[cohort.pr_id.isin(keep)]
    aidev = duckdb.sql(f"""SELECT pr_id, list(sha) AS shas FROM '{RAW / 'pr_commits.parquet'}'
                           WHERE pr_id IN (SELECT DISTINCT pr_id FROM cohort) GROUP BY 1""").df()
    aidev_commits = dict(zip(aidev.pr_id, aidev.shas.map(list)))
    repos = sorted(cohort.repo.unique())
    print(f"{cohort.pr_id.nunique()} PRs in {len(repos)} repos (git, no token)")

    results, failures = [], []
    with ThreadPoolExecutor(args.workers) as ex:
        futs = {ex.submit(process_repo, r, cohort[cohort.repo == r], aidev_commits): r for r in repos}
        for f in tqdm(as_completed(futs), total=len(futs), desc="repos"):
            try:
                results.append(f.result())
            except Exception as e:  # keep going; record the repo as failed
                failures.append((futs[f], repr(e)[:200]))
    plan = pd.DataFrame([row for r in results for row in r["plan"]])
    commits = pd.DataFrame([row for r in results for row in r["commits"]])
    plan.to_parquet(MANIFEST, index=False)
    commits.to_parquet(PR_META, index=False)
    print(plan.groupby("pr_status").pr_id.nunique().rename("PRs").to_string())
    print(plan.fetch_status.value_counts().to_string())
    if failures:
        print(f"{len(failures)} repos raised errors (re-run to retry):", failures[:5])
    print(f"wrote {MANIFEST.relative_to(INTERIM.parent.parent)} ({len(plan)} rows, {plan.pr_id.nunique()} PRs)")


if __name__ == "__main__":
    main()
