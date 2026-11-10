"""Independent re-implementation of Ghaleb (2026) RQ1-RQ3 on the pinned AIDev-pop snapshot.

Semantics follow the original scripts (external/ghaleb-replication/code/rq_*.py) exactly,
including their quirks, so numbers are comparable. Every reported value is printed next
to the value published in the paper (arXiv:2601.17413v1) and written to
results/replication/.

Usage:  uv run python -m cicd_rai.replicate
"""

from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pandas as pd
from scipy.stats import chi2_contingency

from cicd_rai.ci_patterns import identify_ci_service, is_github_actions

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "aidev"
RUNS_CSV = ROOT / "external" / "ghaleb-replication" / "data" / "all_agentic_prs_ci_output_worklfows.csv"
OUT = ROOT / "results" / "replication"

# rq_2.py uses a shorter, 11-platform list (15 patterns) to define "CI YAML Changes".
RQ2_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\.github/workflows/.*\.ya?ml$", r"\.travis\.ya?ml$",
        r"\.circleci/config\.ya?ml$", r"circle\.ya?ml$", r"\.gitlab-ci\.ya?ml$",
        r"\.appveyor\.ya?ml$", r"^appveyor\.ya?ml$",
        r"azure-pipelines.*\.ya?ml$", r"\.azure-pipelines.*\.ya?ml$",
        r"bitbucket-pipelines\.ya?ml$", r"Jenkinsfile", r"jenkins\.ya?ml$",
        r"\.drone\.ya?ml$", r"\.buildkite/.*\.ya?ml$", r"buildkite\.ya?ml$", r"\.cirrus\.ya?ml$",
    ]
]

# Values published in the paper, used only for side-by-side comparison.
PAPER = {
    "rq1_total_files": 711_923, "rq1_yaml_files": 23_161, "rq1_yaml_pct": 3.25,
    "rq1_ci_files": 7_817, "rq1_ci_pct_of_yaml": 33.75, "rq1_chi2_agent_yaml": 3016.26,
    "rq1_chi2_agent_platform": 435.38, "rq1_projects": 1_605, "rq1_yaml_prs": 8_031,
    "rq1_mean_file_changes": 17.63, "rq1_median_file_changes": 3.0, "rq1_max_file_changes": 3_589,
    "rq1_pct_one_pr_projects": 47.0, "rq1_gha_share_pct": 96.77,
    "rq2_ci_merged": 1_615, "rq2_ci_total": 2_383, "rq2_ci_rate": 67.77,
    "rq2_nonci_merged": 22_399, "rq2_nonci_total": 31_197, "rq2_nonci_rate": 71.80,
    "rq2_chi2": 12.95, "rq2_odds_ratio": 1.21,
    "rq3_completed_runs": 99_930, "rq3_ci_rate": 75.59, "rq3_nonci_rate": 74.87,
    "rq3_chi2": 2.20, "rq3_p": 0.138,
}
PAPER_RQ1_AGENT = {  # total, yaml, yaml%
    "Devin": (169_467, 8_186, 4.83), "Cursor": (37_838, 1_661, 4.39), "Claude_Code": (23_296, 972, 4.17),
    "Copilot": (187_247, 6_426, 3.43), "OpenAI_Codex": (294_075, 5_916, 2.01),
}
PAPER_RQ2_AGENT = {  # ci%, nonci%, diff, chi2
    "OpenAI_Codex": (77.01, 82.89, -5.87, 2.91), "Devin": (63.37, 52.52, 10.85, 23.58),
    "Claude_Code": (61.02, 58.90, 2.12, 0.03), "Cursor": (60.00, 65.74, -5.74, 1.49),
    "Copilot": (58.67, 40.92, 17.75, 67.03),
}
PAPER_RQ3_AGENT = {  # no-ci%, with-ci%, diff, chi2
    "Claude_Code": (72.36, 78.44, 6.08, 3.81), "Copilot": (68.77, 74.75, 5.98, 40.62),
    "Cursor": (79.10, 83.13, 4.03, 4.80), "Devin": (81.26, 83.16, 1.91, 5.89),
    "OpenAI_Codex": (64.12, 64.00, -0.12, 0.01),
}

_rows: list[dict] = []


def report(metric: str, ours, paper=None) -> None:
    match = None
    if paper is not None and ours is not None:
        tol = 0.01 if isinstance(paper, float) else 0
        match = abs(float(ours) - float(paper)) <= tol + 1e-9
    _rows.append({"metric": metric, "ours": ours, "paper": paper, "match": match})
    flag = "" if match is None else ("  ok" if match else "  <-- DIFFERS")
    print(f"  {metric:<42} ours={ours!s:<12} paper={paper!s:<10}{flag}")


def chi2(table) -> tuple[float, float, int]:
    c, p, dof, _ = chi2_contingency(table)  # scipy default: Yates correction on 2x2, as in the original
    return round(float(c), 2), float(p), int(dof)


def load(con: duckdb.DuckDBPyConnection) -> None:
    con.sql(f"CREATE VIEW prs AS SELECT id, agent, repo_id, merged_at FROM '{RAW / 'pull_request.parquet'}'")
    con.sql(f"CREATE VIEW files_raw AS SELECT pr_id, sha, filename FROM '{RAW / 'pr_commit_details.parquet'}'")
    # Classify each distinct filename with the original Python regex functions (avoids regex-dialect drift).
    names = con.sql("SELECT DISTINCT filename FROM files_raw WHERE filename IS NOT NULL").df()
    names["ci_service"] = names["filename"].map(identify_ci_service)
    names["is_gha"] = names["filename"].map(is_github_actions)
    names["is_ci_rq2"] = names["filename"].map(lambda f: any(p.search(f) for p in RQ2_PATTERNS))
    con.register("name_class", names)
    con.sql("""
        CREATE TABLE files AS
        SELECT f.pr_id, f.sha, f.filename, p.agent, p.repo_id,
               coalesce(suffix(f.filename, '.yml') OR suffix(f.filename, '.yaml'), false) AS is_yaml,
               n.ci_service, coalesce(n.is_gha, false) AS is_gha, coalesce(n.is_ci_rq2, false) AS is_ci_rq2
        FROM files_raw f
        LEFT JOIN prs p ON f.pr_id = p.id
        LEFT JOIN name_class n USING (filename)
    """)


def rq1(con) -> None:
    print("\nRQ1  CI/CD engagement")
    tot, yml, ci = con.sql(
        "SELECT count(*), sum(is_yaml::INT), count(ci_service) FROM files").fetchall()[0]
    report("total file-change rows", tot, PAPER["rq1_total_files"])
    report("YAML rows", yml, PAPER["rq1_yaml_files"])
    report("YAML % of rows", round(100 * yml / tot, 2), PAPER["rq1_yaml_pct"])
    report("CI/CD rows (24-service regex)", ci, PAPER["rq1_ci_files"])
    report("CI/CD % of YAML rows", round(100 * ci / yml, 2), PAPER["rq1_ci_pct_of_yaml"])
    report("CI/CD % of ALL rows (abstract says 3.25%)", round(100 * ci / tot, 2))

    agents = con.sql("""
        SELECT agent, count(*) AS total_files, sum(is_yaml::INT) AS yaml_files, count(ci_service) AS ci_files
        FROM files WHERE agent IS NOT NULL GROUP BY agent ORDER BY agent""").df()
    agents["yaml_pct"] = (100 * agents.yaml_files / agents.total_files).round(2)
    for r in agents.itertuples():
        t, y, pct = PAPER_RQ1_AGENT.get(r.agent, (None, None, None))
        report(f"{r.agent}: total rows", r.total_files, t)
        report(f"{r.agent}: YAML rows", r.yaml_files, y)
        report(f"{r.agent}: YAML %", r.yaml_pct, pct)
    c, p, dof = chi2(pd.DataFrame({"y": agents.yaml_files, "n": agents.total_files - agents.yaml_files}).values)
    report(f"chi2 agent x YAML (df={dof})", c, PAPER["rq1_chi2_agent_yaml"])
    agents.to_csv(OUT / "rq1_agent_yaml_rates.csv", index=False)

    platforms = con.sql("""
        SELECT ci_service, count(*) AS n FROM files WHERE ci_service IS NOT NULL
        GROUP BY ci_service ORDER BY n DESC""").df()
    platforms["pct"] = (100 * platforms.n / platforms.n.sum()).round(2)
    report("GitHub Actions share of CI/CD rows %", float(platforms.set_index("ci_service").pct["GitHub Actions"]),
           PAPER["rq1_gha_share_pct"])
    print(platforms.to_string(index=False))
    platforms.to_csv(OUT / "rq1_ci_distribution.csv", index=False)

    matrix = con.sql("""
        PIVOT (SELECT agent, ci_service FROM files WHERE ci_service IS NOT NULL AND agent IS NOT NULL)
        ON ci_service USING count(*) GROUP BY agent ORDER BY agent""").df().set_index("agent").fillna(0)
    c, p, dof = chi2(matrix.values)
    report(f"chi2 agent x platform (df={dof})", c, PAPER["rq1_chi2_agent_platform"])
    matrix.to_csv(OUT / "rq1_agent_platform_matrix.csv")

    proj = con.sql("""
        SELECT repo_id, count(*) AS file_changes, count(DISTINCT pr_id) AS num_prs,
               count(DISTINCT sha) AS num_commits, count(DISTINCT filename) AS unique_yaml_files
        FROM files WHERE is_yaml AND repo_id IS NOT NULL GROUP BY repo_id""").df()
    report("projects with YAML changes", len(proj), PAPER["rq1_projects"])
    report("PRs with YAML changes", int(proj.num_prs.sum()), PAPER["rq1_yaml_prs"])
    report("mean YAML file-changes / project", round(proj.file_changes.mean(), 2), PAPER["rq1_mean_file_changes"])
    report("median YAML file-changes / project", float(proj.file_changes.median()), PAPER["rq1_median_file_changes"])
    report("max YAML file-changes / project", int(proj.file_changes.max()), PAPER["rq1_max_file_changes"])
    report("% projects with exactly 1 YAML PR", round(100 * (proj.num_prs == 1).mean(), 1),
           PAPER["rq1_pct_one_pr_projects"])
    proj.to_csv(OUT / "rq1_project_stats.csv", index=False)


def rq2(con) -> None:
    print("\nRQ2  Merge rates (CI = 11-platform list from rq_2.py)")
    pr = con.sql("""
        SELECT p.id AS pr_id, p.agent, p.merged_at IS NOT NULL AS is_merged,
               CASE WHEN bool_or(f.is_ci_rq2) THEN 'CI' ELSE 'NonCI' END AS cat
        FROM prs p JOIN files f ON f.pr_id = p.id
        GROUP BY ALL""").df()
    n_prs = con.sql("SELECT count(*) FROM prs").fetchall()[0][0]
    report("PRs without any file rows (dropped)", n_prs - len(pr))
    g = pr.groupby("cat").is_merged.agg(["sum", "count"])
    report("CI PRs merged", int(g.loc["CI", "sum"]), PAPER["rq2_ci_merged"])
    report("CI PRs total", int(g.loc["CI", "count"]), PAPER["rq2_ci_total"])
    report("NonCI PRs merged", int(g.loc["NonCI", "sum"]), PAPER["rq2_nonci_merged"])
    report("NonCI PRs total", int(g.loc["NonCI", "count"]), PAPER["rq2_nonci_total"])
    report("CI merge rate %", round(100 * g.loc["CI", "sum"] / g.loc["CI", "count"], 2), PAPER["rq2_ci_rate"])
    report("NonCI merge rate %", round(100 * g.loc["NonCI", "sum"] / g.loc["NonCI", "count"], 2),
           PAPER["rq2_nonci_rate"])
    tab = pd.crosstab(pr.cat, pr.is_merged)
    c, p, _ = chi2(tab)
    report("chi2 overall", c, PAPER["rq2_chi2"])
    a, b, cc, d = tab.iloc[0, 0], tab.iloc[0, 1], tab.iloc[1, 0], tab.iloc[1, 1]
    report("odds ratio (as in rq_2.py)", round(a * d / (b * cc), 2), PAPER["rq2_odds_ratio"])
    rows = []
    for agent, sub in pr.groupby("agent"):
        t = pd.crosstab(sub.cat, sub.is_merged)
        c, p, _ = chi2(t)
        rates = sub.groupby("cat").is_merged.mean() * 100
        ci_r, non_r = round(rates.get("CI", float("nan")), 2), round(rates.get("NonCI", float("nan")), 2)
        exp = PAPER_RQ2_AGENT.get(agent, (None,) * 4)
        report(f"{agent}: CI merge %", ci_r, exp[0])
        report(f"{agent}: NonCI merge %", non_r, exp[1])
        report(f"{agent}: chi2 (p={p:.4f})", c, exp[3])
        rows.append({"agent": agent, "ci_rate": ci_r, "nonci_rate": non_r, "diff_pp": round(ci_r - non_r, 2),
                     "chi2": c, "p": p, "n_ci": int((sub.cat == "CI").sum()), "n_nonci": int((sub.cat == "NonCI").sum())})
    pd.DataFrame(rows).to_csv(OUT / "rq2_agent_merge_rates.csv", index=False)


def rq3(con) -> None:
    print("\nRQ3  Workflow-run success (GitHub Actions only, run-level)")
    runs = pd.read_csv(RUNS_CSV, low_memory=False)
    runs = runs[[c for c in runs.columns if not c.startswith("Unnamed")]]
    con.register("runs_df", runs)
    con.sql("""
        CREATE TABLE runs AS
        SELECT r.*, p.agent, (r.pr_id IN (SELECT pr_id FROM files WHERE is_gha)) AS modified_gha
        FROM runs_df r LEFT JOIN prs p ON r.pr_id = p.id""")
    total, completed = con.sql(
        "SELECT count(*), count(*) FILTER (status = 'completed') FROM runs").fetchall()[0]
    report("workflow runs in CSV", total)
    report("completed runs", completed, PAPER["rq3_completed_runs"])
    done = con.sql("SELECT pr_id, agent, modified_gha, conclusion = 'success' AS ok FROM runs "
                   "WHERE status = 'completed'").df()
    rate = done.groupby("modified_gha").ok.mean() * 100
    report("success % (modified GHA)", round(rate[True], 2), PAPER["rq3_ci_rate"])
    report("success % (did not modify)", round(rate[False], 2), PAPER["rq3_nonci_rate"])
    c, p, _ = chi2(pd.crosstab(done.modified_gha, done.ok))
    report("chi2 overall", c, PAPER["rq3_chi2"])
    report("p overall", round(p, 3), PAPER["rq3_p"])
    rows = []
    for agent, sub in done.groupby("agent"):
        c, p, _ = chi2(pd.crosstab(sub.modified_gha, sub.ok))
        r = sub.groupby("modified_gha").ok.mean() * 100
        exp = PAPER_RQ3_AGENT.get(agent, (None,) * 4)
        report(f"{agent}: no-CI success %", round(r.get(False, float("nan")), 2), exp[0])
        report(f"{agent}: with-CI success %", round(r.get(True, float("nan")), 2), exp[1])
        report(f"{agent}: chi2 (p={p:.4f})", c, exp[3])
        rows.append({"agent": agent, "no_ci_rate": round(r.get(False), 2), "with_ci_rate": round(r.get(True), 2),
                     "chi2": c, "p": p})
    pd.DataFrame(rows).to_csv(OUT / "rq3_agent_success_rates.csv", index=False)

    # Correction (ours): the CSV's first column is author_type; 24,860 completed runs come from
    # 5,233 *human* PRs that are not in AIDev-pop's agent tables. rq_3.py cannot see their files,
    # so they all land in "Did Not Modify CI YAML" and inflate that group's success rate.
    agent_only = done[done.agent.notna()]
    ar = agent_only.groupby("modified_gha").ok.mean() * 100
    c, p, _ = chi2(pd.crosstab(agent_only.modified_gha, agent_only.ok))
    report("[correction] runs from human PRs in CSV", int(done.agent.isna().sum()))
    report("[correction] completed runs, agent PRs only", len(agent_only))
    report("[correction] success % modified (agent only)", round(ar[True], 2))
    report("[correction] success % not modified (agent only)", round(ar[False], 2))
    report(f"[correction] chi2 agent only (naive, p={p:.2g})", c)

    # Robustness (ours, not in the paper): runs are clustered within PRs, so a PR with 300 runs
    # weighs 300x. Re-estimate at PR level using each PR's share of successful runs (agent PRs only;
    # clustered models are in cicd_rai.rq3_robust).
    pr_level = agent_only.groupby(["pr_id", "modified_gha"]).ok.mean().reset_index()
    pr_rate = pr_level.groupby("modified_gha").ok.mean() * 100
    report("[robustness] PR-level mean success % (modified)", round(pr_rate[True], 2))
    report("[robustness] PR-level mean success % (not modified)", round(pr_rate[False], 2))
    report("[robustness] PRs with runs (modified / not)",
           f"{int(pr_level.modified_gha.sum())}/{int((~pr_level.modified_gha).sum())}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    load(con)
    rq1(con)
    rq2(con)
    rq3(con)
    df = pd.DataFrame(_rows)
    df.to_csv(OUT / "comparison_with_paper.csv", index=False)
    checked = df[df.match.notna()]
    print(f"\n{int(checked.match.sum())}/{len(checked)} published values reproduced exactly "
          f"(tolerance 0.01). Differences: {list(checked[~checked.match.astype(bool)].metric)}")


if __name__ == "__main__":
    main()
