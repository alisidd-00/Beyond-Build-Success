"""Robustness of Ghaleb's RQ3 ("CI/CD-changing PRs have comparable/slightly higher build success").

The original analysis pools 99,930 workflow runs and runs a chi-square test, treating runs as
independent. Runs are clustered within PRs (and PRs within repositories), and the success rate
counts 'skipped'/'cancelled'/'action_required' runs as non-successes. We re-test the claim:

  A. run level, as published (chi-square)                       -> should reproduce +0.72 pp, p = .138
  B. run level, logistic GEE clustered by PR, and by repository -> correct standard errors
  C. B + agent fixed effects                                    -> agent mix as confounder
  D. PR level: first run success; all runs succeeded            -> one observation per PR
  E. strict denominator: success / (success + failure) only     -> excludes skipped/cancelled/etc.

Usage:  uv run python -m cicd_rai.rq3_robust      (writes results/replication/rq3_robustness.csv)
"""

from __future__ import annotations

import warnings
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from scipy.stats import chi2_contingency

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "aidev"
RUNS_CSV = ROOT / "external" / "ghaleb-replication" / "data" / "all_agentic_prs_ci_output_worklfows.csv"
OUT = ROOT / "results" / "replication" / "rq3_robustness.csv"
FAILURE = ("failure", "timed_out", "startup_failure")

rows: list[dict] = []


def add(analysis: str, unit: str, n: int, rate_mod: float, rate_not: float, effect: str, p: float, note=""):
    rows.append({"analysis": analysis, "unit": unit, "n": n, "success_modified_pct": round(rate_mod, 2),
                 "success_not_modified_pct": round(rate_not, 2), "diff_pp": round(rate_mod - rate_not, 2),
                 "effect": effect, "p": p, "note": note})
    print(f"{analysis:<48} n={n:>6}  mod={rate_mod:6.2f}%  not={rate_not:6.2f}%  "
          f"diff={rate_mod - rate_not:+6.2f}pp  {effect:<22} p={p:.3g}  {note}")


def load() -> pd.DataFrame:
    con = duckdb.connect()
    return con.sql(f"""
        WITH gha AS (SELECT DISTINCT pr_id FROM '{RAW / 'pr_commit_details.parquet'}'
                     WHERE regexp_matches(filename, '\\.github/workflows/.*\\.ya?ml$', 'i'))
        SELECT r.pr_id::BIGINT AS pr_id, p.repo_id, p.agent, r.conclusion,
               try_cast(r.started_at AS TIMESTAMP) AS started_at, r.run_id,
               (r.pr_id::BIGINT IN (SELECT pr_id FROM gha))::INT AS modified,
               (r.conclusion = 'success')::INT AS ok,
               (r.conclusion IN {FAILURE})::INT AS failed
        FROM read_csv('{RUNS_CSV}', all_varchar = true) r
        JOIN '{RAW / 'pull_request.parquet'}' p ON p.id = r.pr_id::BIGINT
        WHERE r.status = 'completed'""").df()


def gee(df: pd.DataFrame, formula: str, group: str) -> tuple[float, float]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m = smf.gee(formula, groups=df[group], data=df, family=sm.families.Binomial(),
                    cov_struct=sm.cov_struct.Exchangeable()).fit()
    return float(np.exp(m.params["modified"])), float(m.pvalues["modified"])


def main() -> None:
    df = load()
    rate = lambda d, col="ok": 100 * d.groupby("modified")[col].mean()  # noqa: E731

    # A. as published
    r = rate(df)
    c, p, _, _ = chi2_contingency(pd.crosstab(df.modified, df.ok))
    add("A  run-level chi2 (published method)", "run", len(df), r[1], r[0], f"chi2={c:.2f}", p)

    # B/C. clustered logistic models
    for group in ("pr_id", "repo_id"):
        orr, p = gee(df, "ok ~ modified", group)
        add(f"B  run-level GEE, clustered by {group}", "run", len(df), r[1], r[0], f"OR={orr:.3f}", p)
    orr, p = gee(df, "ok ~ modified + C(agent)", "repo_id")
    add("C  GEE by repo + agent fixed effects", "run", len(df), r[1], r[0], f"OR={orr:.3f}", p)

    # D. one observation per PR
    first = df.sort_values(["pr_id", "started_at", "run_id"]).groupby("pr_id").head(1)
    r1 = rate(first)
    c, p, _, _ = chi2_contingency(pd.crosstab(first.modified, first.ok))
    add("D1 PR-level: first run succeeded", "PR", len(first), r1[1], r1[0], f"chi2={c:.2f}", p)
    allok = df.groupby(["pr_id", "modified", "repo_id", "agent"]).ok.min().reset_index()
    r2 = rate(allok)
    orr, p = gee(allok, "ok ~ modified + C(agent)", "repo_id")
    add("D2 PR-level: all runs succeeded (GEE repo+agent)", "PR", len(allok), r2[1], r2[0], f"OR={orr:.3f}", p)

    # E. strict denominator (decisive outcomes only)
    strict = df[(df.ok == 1) | (df.failed == 1)]
    r3 = rate(strict)
    orr, p = gee(strict, "ok ~ modified + C(agent)", "repo_id")
    add("E  success/(success+failure), GEE repo+agent", "run", len(strict), r3[1], r3[0], f"OR={orr:.3f}", p,
        f"dropped {len(df) - len(strict)} skipped/cancelled/other runs")

    # per agent, PR level first run (the unit the paper's per-agent claims should hold at)
    for agent, sub in first.groupby("agent"):
        rr = rate(sub)
        if sub.modified.nunique() == 2:
            c, p, _, _ = chi2_contingency(pd.crosstab(sub.modified, sub.ok))
            add(f"D1 {agent}: first run succeeded", "PR", len(sub), rr[1], rr[0], f"chi2={c:.2f}", p)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(OUT, index=False)
    print(f"\nwrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
