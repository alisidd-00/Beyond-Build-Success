"""Cohort-level summary of the security-delta extension (tables for the short paper).

Inputs : data/interim/{revision_manifest,findings,deltas,pr_evidence,revision_evidence}.parquet
Outputs: results/extension/*.csv  (aggregates only: no usernames, no file contents)

Finding tiers (zizmor personas; actionlint/yaml findings count as 'all' only):
  all      every finding (auditor persona)  -- what a maximal scan reports
  default  zizmor 'Regular' persona         -- what zizmor shows by default
  high     default AND severity High        -- the strictest tier
  *_no_pin same, excluding 'unpinned-uses'  -- sensitivity: tag-pinned actions are common practice

Associations are reported raw (chi-square) and adjusted (logistic, repo-clustered SEs, controls:
agent, log #commits, #workflow files, whether a workflow file was added).

Usage:  uv run python -m cicd_rai.summarize
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from scipy.stats import chi2_contingency

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "aidev"
INTERIM = ROOT / "data" / "interim"
OUT = ROOT / "results" / "extension"
TIERS = ("all", "default", "high", "default_no_pin", "high_no_pin")


def tier_flags(findings: pd.DataFrame) -> pd.DataFrame:
    meta = findings.drop_duplicates("key")[["key", "tool", "rule", "persona", "severity"]].copy()
    meta["default"] = (meta.tool == "zizmor") & (meta.persona == "Regular")
    meta["high"] = meta["default"] & (meta.severity == "High")
    meta["all"] = True
    meta["default_no_pin"] = meta["default"] & (meta.rule != "unpinned-uses")
    meta["high_no_pin"] = meta["high"] & (meta.rule != "unpinned-uses")
    return meta.set_index("key")


def count_tiers(keys, meta: pd.DataFrame) -> dict:
    keys = list(keys) if keys is not None else []
    known = [k for k in keys if k in meta.index]
    sub = meta.loc[known] if known else meta.iloc[0:0]
    return {t: int(sub[t].sum()) for t in TIERS}


def pr_level(deltas: pd.DataFrame, meta: pd.DataFrame, comparison: str) -> pd.DataFrame:
    d = deltas[(deltas.comparison == comparison) & (deltas.evidence == "complete")].copy()
    for name in ("introduced", "fixed"):
        tiers = pd.DataFrame([count_tiers(k, meta) for k in d[f"{name}_keys"]], index=d.index)
        for t in TIERS:
            d[f"{name}_{t}"] = tiers[t]
    d["added_file"] = (d.file_change == "added").astype(int)
    agg = {f"{n}_{t}": "sum" for n in ("introduced", "fixed") for t in TIERS}
    agg.update({"n_after": "sum", "n_introduced": "sum", "path": "nunique", "added_file": "max"})
    out = d.groupby(["pr_id", "agent"]).agg(agg).rename(columns={"path": "n_workflow_files"}).reset_index()
    return out


def rate_table(df: pd.DataFrame, by: str, flag: str) -> pd.DataFrame:
    g = df.groupby(by)[flag].agg(["sum", "count"])
    g["pct"] = (100 * g["sum"] / g["count"]).round(2)
    return g.rename(columns={"sum": "n_with", "count": "n_prs"})


def assoc(df: pd.DataFrame, flag: str, outcome: str) -> dict:
    t = pd.crosstab(df[flag], df[outcome])
    if t.shape != (2, 2):
        return {"flag": flag, "outcome": outcome, "note": "degenerate table"}
    c, p, _, _ = chi2_contingency(t)
    r = df.groupby(flag)[outcome].mean() * 100
    return {"flag": flag, "outcome": outcome, "pct_if_flag": round(r[True], 2), "pct_if_not": round(r[False], 2),
            "diff_pp": round(r[True] - r[False], 2), "chi2": round(c, 2), "p": p,
            "n_flag": int(df[flag].sum()), "n": len(df)}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = pd.read_parquet(INTERIM / "revision_manifest.parquet")
    findings = pd.read_parquet(INTERIM / "findings.parquet")
    deltas = pd.read_parquet(INTERIM / "deltas.parquet")
    prs_ev = pd.read_parquet(INTERIM / "pr_evidence.parquet")
    rev_ev = pd.read_parquet(INTERIM / "revision_evidence.parquet")
    meta = tier_flags(findings)

    # ---- 1. coverage ------------------------------------------------------------------------------
    cov = manifest.groupby("agent").agg(prs=("pr_id", "nunique"),
                                        prs_history_ok=("pr_status", lambda s: s.eq("ok").groupby(manifest.loc[s.index, "pr_id"]).all().sum()))
    cov["file_versions"] = manifest.groupby("agent").size()
    cov["versions_ok_or_absent"] = manifest[manifest.fetch_status.isin(["ok", "absent"])].groupby("agent").size()
    cov.loc["Total"] = cov.sum()
    cov.to_csv(OUT / "coverage.csv")
    print("== coverage\n", cov.to_string())
    print("PR status:", manifest.drop_duplicates("pr_id").pr_status.str.split(":").str[0].value_counts().to_dict())

    # ---- 2. introduced risks per PR ---------------------------------------------------------------
    pr = pr_level(deltas, meta, "pr_delta")
    for t in TIERS:
        pr[f"intro_{t}"] = pr[f"introduced_{t}"] > 0
    tab = pd.concat({t: rate_table(pr, "agent", f"intro_{t}")["pct"] for t in TIERS}, axis=1)
    tab["n_prs"] = pr.groupby("agent").size()
    overall = {t: round(100 * pr[f"intro_{t}"].mean(), 2) for t in TIERS} | {"n_prs": len(pr)}
    tab.loc["Total"] = pd.Series(overall)
    tab.to_csv(OUT / "introduced_by_agent.csv")
    print("\n== % of PRs introducing >=1 finding (pr_delta: base -> final)\n", tab.to_string())

    # ---- 3. which rules get introduced -------------------------------------------------------------
    d = deltas[(deltas.comparison == "pr_delta") & (deltas.evidence == "complete")]
    intro = d[["pr_id", "agent", "introduced_keys"]].explode("introduced_keys").dropna()
    intro = intro.join(meta, on="introduced_keys")
    rules = (intro.groupby(["tool", "rule", "persona", "severity"], dropna=False)
             .agg(findings=("pr_id", "size"), prs=("pr_id", "nunique")).reset_index()
             .sort_values("prs", ascending=False))
    rules.to_csv(OUT / "introduced_rules.csv", index=False)
    print("\n== most frequently introduced rules\n", rules.head(15).to_string(index=False))

    # ---- 4. alert volume: current-file scan vs delta ----------------------------------------------
    vol = pd.DataFrame({
        "current_file_alerts_total": [int(pr.n_after.sum())],
        "introduced_alerts_total": [int(pr.n_introduced.sum())],
        "median_current_per_pr": [pr.n_after.median()],
        "median_introduced_per_pr": [pr.n_introduced.median()],
    })
    vol["reduction_pct"] = (100 * (1 - vol.introduced_alerts_total / vol.current_file_alerts_total)).round(1)
    vol.to_csv(OUT / "alert_volume.csv", index=False)
    print("\n== alert volume (all tiers)\n", vol.to_string(index=False))

    # ---- 5. first attempt vs revision ------------------------------------------------------------
    first = pr_level(deltas, meta, "first_attempt")
    revn = pr_level(deltas, meta, "revision")
    fr = first[["pr_id", "introduced_default"]].merge(revn[["pr_id", "introduced_default", "fixed_default"]],
                                                      on="pr_id", suffixes=("_first", "_rev"))
    fb = (rev_ev[rev_ev.role.str.endswith("final")].groupby("pr_id")
          .agg(human_feedback=("human_feedback_before", "max")).reset_index())
    fr = fr.merge(fb, on="pr_id", how="left")
    fr["had_feedback"] = fr.human_feedback.fillna(0) > 0
    traj = fr.assign(first_risky=fr.introduced_default_first > 0, fixed_later=fr.fixed_default > 0,
                     added_later=fr.introduced_default_rev > 0)
    traj_tab = traj.groupby("had_feedback")[["first_risky", "fixed_later", "added_later"]].mean().mul(100).round(2)
    traj_tab["n_prs"] = traj.groupby("had_feedback").size()
    traj_tab.to_csv(OUT / "first_attempt_vs_revision.csv")
    print("\n== first attempt vs later revision (default tier), by independent human feedback\n", traj_tab.to_string())

    # ---- 6. associations with outcomes -------------------------------------------------------------
    final_ci = (rev_ev[rev_ev.role.str.endswith("final")].groupby("pr_id").ci_outcome
                .agg(lambda s: "failure" if (s == "failure").any() else
                     "success" if (s == "success").all() else "unknown"))
    df = pr.merge(prs_ev, on=["pr_id", "agent"], how="left").merge(final_ci.rename("final_ci"), on="pr_id", how="left")
    df["ci_failed"] = df.final_ci == "failure"
    known_ci = df[df.final_ci.isin(["failure", "success"])]
    rows = []
    for t in TIERS:
        rows.append(assoc(df[df.outcome != "open"], f"intro_{t}", "merged"))
        rows.append(assoc(known_ci, f"intro_{t}", "ci_failed"))
        rows.append(assoc(df, f"intro_{t}", "any_independent_human_oversight"))
    assoc_df = pd.DataFrame(rows)
    assoc_df.to_csv(OUT / "associations.csv", index=False)
    print("\n== associations (raw chi-square, PR level)\n", assoc_df.to_string(index=False))

    # ---- 7. adjusted associations ----------------------------------------------------------------
    repo = pd.read_parquet(RAW / "pull_request.parquet", columns=["id", "repo_id"]).rename(columns={"id": "pr_id"})
    x = df.merge(repo, on="pr_id")
    x["log_commits"] = np.log1p(x.n_commits_api)
    x["merged_i"] = x.merged.astype(int)
    x["ci_fail_i"] = x.ci_failed.astype(int)
    x["human_i"] = x.any_independent_human_oversight.astype(int)
    adj = []
    for t in ("default", "high", "default_no_pin", "high_no_pin"):
        x["risk"] = x[f"intro_{t}"].astype(int)
        for y, sub in [("merged_i", x[x.outcome != "open"]), ("ci_fail_i", x[x.final_ci.isin(["failure", "success"])]),
                       ("human_i", x)]:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                m = smf.logit(f"{y} ~ risk + C(agent) + log_commits + n_workflow_files + added_file", data=sub).fit(
                    disp=0, cov_type="cluster", cov_kwds={"groups": sub.repo_id})
            lo, hi = np.exp(m.conf_int().loc["risk"])
            adj.append({"tier": t, "outcome": y.removesuffix("_i"), "odds_ratio": round(float(np.exp(m.params["risk"])), 3),
                        "ci_low": round(float(lo), 3), "ci_high": round(float(hi), 3), "p": float(m.pvalues["risk"]),
                        "n": len(sub)})
    adj_df = pd.DataFrame(adj)
    adj_df.to_csv(OUT / "associations_adjusted.csv", index=False)
    print("\n== adjusted associations (logit, repo-clustered SE; agent + size controls)\n", adj_df.to_string(index=False))
    print(f"\nfinal CI evidence: {df.final_ci.value_counts(dropna=False).to_dict()}")
    print(f"wrote {OUT.relative_to(ROOT)}/*.csv")


if __name__ == "__main__":
    main()
