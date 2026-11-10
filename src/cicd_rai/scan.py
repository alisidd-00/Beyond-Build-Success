"""Scan every fetched workflow revision with zizmor + actionlint and diff findings across revisions.

Inputs : data/interim/revision_manifest.parquet  (from scripts/fetch_revisions.py)
Outputs: data/interim/findings.parquet  one row per finding per file revision
         data/interim/deltas.parquet    per (PR, path, comparison): introduced / fixed / persisting

Scanners run fully offline (zizmor --offline, no config, auditor persona so that *all*
findings are recorded; persona/severity/confidence are kept as columns so baselines can
filter later). Files are only read, never executed.

Finding identity across revisions: line numbers shift when an agent edits a workflow, so a
finding is keyed by (tool, rule, normalised YAML route, annotation). Step indices in the
route are replaced by the step's `id`, else `name`, else `uses`, else a hash of `run`, so
inserting a step does not make every later finding look "new".

Usage:  uv run python -m cicd_rai.scan
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path

import pandas as pd
import yaml
from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[2]
INTERIM = ROOT / "data" / "interim"
BY_SHA = INTERIM / "revisions" / "_by_sha"
BATCH = 150

COMPARISONS = [  # (name, before-role, after-role)
    ("pr_delta", "base", "final"),          # what the PR as a whole introduced
    ("first_attempt", "base", "touch_1"),   # agent's first version of the file
    ("revision", "touch_1", "final"),       # what changed after the first attempt (incl. feedback)
]


def local_path(repo: str, sha: str, path: str) -> Path:
    return BY_SHA / repo.replace("/", "__") / sha / path


# ----------------------------------------------------------------------------- routes
def _step_label(step) -> str:
    if not isinstance(step, dict):
        return "?"
    for k in ("id", "name", "uses"):
        if step.get(k):
            return f"{k}={step[k]}"
    if step.get("run"):
        return "run#" + hashlib.sha1(str(step["run"]).encode()).hexdigest()[:8]
    return "?"


def normalise_route(components: list, doc) -> str:
    out, node = [], doc
    for comp in components:
        key = comp.get("Key") if isinstance(comp, dict) else None
        idx = comp.get("Index") if isinstance(comp, dict) else None
        if key is not None:
            out.append(str(key))
            node = node.get(key) if isinstance(node, dict) else None
        elif idx is not None:
            child = node[idx] if isinstance(node, list) and idx < len(node) else None
            parent = out[-1] if out else ""
            out.append(f"[{_step_label(child)}]" if parent == "steps" else f"[{idx}]")
            node = child
    return ".".join(out)


def load_yaml(p: Path):
    try:
        return yaml.safe_load(p.read_text(errors="replace")), None
    except Exception as e:  # malformed YAML is itself a result worth recording
        return None, type(e).__name__


# ----------------------------------------------------------------------------- scanners
def run_zizmor(files: list[Path]) -> list[dict]:
    cmd = ["zizmor", "--offline", "--no-config", "--persona", "auditor", "--format", "json",
           "--no-exit-codes", "--no-progress", "--quiet", *map(str, files)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return json.loads(res.stdout or "[]")
    except json.JSONDecodeError:
        if len(files) == 1:
            return [{"_error": (res.stderr or "")[-300:], "_file": str(files[0])}]
        # one unparsable file can fail the batch: fall back to per-file
        return [f for p in files for f in run_zizmor([p])]


def run_actionlint(files: list[Path]) -> list[dict]:
    cmd = ["actionlint", "-format", "{{json .}}", "-no-color", *map(str, files)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return json.loads(res.stdout or "[]")
    except json.JSONDecodeError:
        return []


def _short_feature(loc: dict) -> str:
    feat = str(loc["concrete"].get("feature", "")).strip()
    return feat if feat and "\n" not in feat and len(feat) <= 160 else ""


def zizmor_rows(raw: list[dict], docs: dict[str, object]) -> list[dict]:
    rows = []
    for f in raw:
        if "_error" in f:
            rows.append({"file": f["_file"], "tool": "zizmor", "rule": "_scan_error", "detail": f["_error"]})
            continue
        prim = next((l for l in f["locations"] if l["symbolic"]["kind"] == "Primary"), f["locations"][0])
        file = prim["symbolic"]["key"]["Local"]["verbatim_path"]
        comps = prim["symbolic"]["route"]["route"]
        det = f["determinations"]
        rows.append({
            "file": file, "tool": "zizmor", "rule": f["ident"],
            "severity": det["severity"], "confidence": det["confidence"], "persona": det["persona"],
            "route": normalise_route(comps, docs.get(file)),
            # the flagged YAML text (e.g. "secrets.NPM_TOKEN", "actions/checkout@v4") makes keys
            # specific when several findings share a route
            # (only single-line features: for job-level rules the feature is the whole job block,
            #  which would turn any edit to the job into a spurious "new" finding)
            "annotation": prim["symbolic"].get("annotation", "") + " :: " + _short_feature(prim),
            "line": prim["concrete"]["location"]["start_point"]["row"] + 1,
            "detail": f.get("desc", ""),
        })
    return rows


def actionlint_rows(raw: list[dict]) -> list[dict]:
    return [{"file": str(Path(e["filepath"]).resolve()), "tool": "actionlint", "rule": e["kind"],
             "severity": "Error", "confidence": None, "persona": None, "route": None,
             # message text identifies the issue; line is kept only for display
             "annotation": e["message"], "line": e["line"], "detail": e["message"]} for e in raw]


def scan_all(manifest: pd.DataFrame) -> pd.DataFrame:
    ok = manifest[manifest.fetch_status == "ok"][["repo", "sha", "path"]].drop_duplicates()
    files = [local_path(r.repo, r.sha, r.path).resolve() for r in ok.itertuples()]
    rows: list[dict] = []
    for i in tqdm(range(0, len(files), BATCH), desc="scanning"):
        batch = files[i:i + BATCH]
        docs, parse_err = {}, {}
        for p in batch:
            docs[str(p)], parse_err[str(p)] = load_yaml(p)
        rows += zizmor_rows(run_zizmor(batch), docs)
        rows += actionlint_rows(run_actionlint(batch))
        rows += [{"file": f, "tool": "yaml", "rule": "yaml_parse_error", "severity": "Error",
                  "annotation": e, "detail": e} for f, e in parse_err.items() if e]
    findings = pd.DataFrame(rows)
    lookup = {str(local_path(r.repo, r.sha, r.path).resolve()): (r.repo, r.sha, r.path) for r in ok.itertuples()}
    findings[["repo", "sha", "path"]] = pd.DataFrame(findings.file.map(lookup).tolist(), index=findings.index)
    findings["key"] = (findings.tool + "|" + findings.rule + "|" + findings.route.fillna("") + "|"
                       + findings.annotation.fillna(""))
    return findings.drop(columns=["file"])


# ----------------------------------------------------------------------------- deltas
def compute_deltas(manifest: pd.DataFrame, findings: pd.DataFrame) -> pd.DataFrame:
    by_rev = {k: Counter(g.key) for k, g in findings.groupby(["repo", "sha", "path"])}
    m = manifest.copy()
    m["role_base"] = m.role.str.replace("+final", "", regex=False)
    out = []
    for (pr_id, path), g in m.groupby(["pr_id", "path"]):
        roles = {}
        for r in g.itertuples():
            roles[r.role_base] = r
            if r.role.endswith("+final"):
                roles["final"] = r
        for name, before, after in COMPARISONS:
            b, a = roles.get(before), roles.get(after)
            row = {"pr_id": pr_id, "path": path, "comparison": name, "agent": g.agent.iloc[0]}
            if b is None or a is None or b.fetch_status not in ("ok", "absent") or a.fetch_status not in ("ok", "absent"):
                out.append({**row, "evidence": "unknown"})  # never impute missing revisions
                continue
            fb = by_rev.get((b.repo, b.sha, path), Counter()) if b.fetch_status == "ok" else Counter()
            fa = by_rev.get((a.repo, a.sha, path), Counter()) if a.fetch_status == "ok" else Counter()
            introduced, fixed = fa - fb, fb - fa
            out.append({**row, "evidence": "complete",
                        "file_change": ("added" if b.fetch_status == "absent" else
                                        "removed" if a.fetch_status == "absent" else
                                        "unchanged" if b.content_sha256 == a.content_sha256 else "modified"),
                        "n_before": sum(fb.values()), "n_after": sum(fa.values()),
                        "n_introduced": sum(introduced.values()), "n_fixed": sum(fixed.values()),
                        "n_persisting": sum((fa & fb).values()),
                        "introduced_keys": sorted(introduced.elements()), "fixed_keys": sorted(fixed.elements())})
    return pd.DataFrame(out)


def main() -> None:
    manifest = pd.read_parquet(INTERIM / "revision_manifest.parquet")
    findings = scan_all(manifest)
    findings.to_parquet(INTERIM / "findings.parquet", index=False)
    deltas = compute_deltas(manifest, findings)
    deltas.to_parquet(INTERIM / "deltas.parquet", index=False)
    print(f"{len(findings)} findings over {findings[['repo','sha','path']].drop_duplicates().shape[0]} file revisions")
    print(findings.groupby(["tool", "rule"]).size().sort_values(ascending=False).head(25).to_string())
    print(deltas.groupby(["comparison", "evidence"]).agg(n=("pr_id", "size"),
          introduced=("n_introduced", "sum"), fixed=("n_fixed", "sum")).to_string())


if __name__ == "__main__":
    main()
