# Replication discrepancy log

Base: Ghaleb (2026), arXiv:2601.17413v1; package `Taher-Ghaleb/AIAgentsCI_CD-MSR2026` @ `4ae0b54`.
Data: AIDev-pop @ HF `4c65121f` (2025-10-28): 33,596 PRs, 711,923 file-change rows (both match the paper).
Our run: `uv run python -m cicd_rai.replicate` → `results/replication/comparison_with_paper.csv`.

**Result: 65 / 72 published values reproduced exactly** (tolerance 0.01), including every per-agent rate in RQ1–RQ3.

| # | Item | Paper | Package output (theirs) | Ours | Assessment |
|---|---|---|---|---|---|
| D1 | Repos where agents modified YAML | 1,605 | 936 (`results/rq1_project_stats.csv`) | 936 | Paper text ≠ its own package output. Not reproducible from any population we tried (2,807 repos total, 2,806 with file rows, 1,765 with a merged PR). |
| D2 | PRs that modified YAML | 8,031 | 3,995 | 3,995 | Same as D1. |
| D3 | YAML file-changes per project, mean / median | 17.63 / 3.0 | 24.74 / 4.0 | 24.74 / 4.0 | Max (3,589) matches; mean/median do not. |
| D4 | Projects with exactly one YAML PR ("one-and-done") | 47 % | 51.3 % | 51.3 % | Direction of the claim holds; value differs. |
| D5 | RQ2 overall χ² | 12.95 | — | 17.42 (Yates) / 17.62 (no Yates) | Paper's own 2×2 counts (1,615/2,383 vs 22,399/31,197, which we match exactly) cannot give 12.95. Conclusion (p < 0.001) unchanged. |
| D6 | RQ2 OpenAI Codex χ² | 2.91 (p < 0.0001) | — | 22.91 (p < 0.0001) | Typo in Table 2 (dropped digit); the stated p-value is consistent with 22.91. |
| D7 | Copilot merge advantage | 15.63 pp (abstract, intro, conclusion) | 17.75 pp (`rq2_impact_analysis.csv`) | 17.75 pp | Table 2 says 17.75; `rq_3.py` hard-codes 15.63 "from RQ2". |
| D8 | "CI/CD configuration files account for 3.25 % of agent changes" (abstract) | 3.25 % | — | YAML = 3.25 %; CI/CD = 1.10 % | 3.25 % is the share of *YAML* rows; CI/CD rows are 7,817 / 711,923. |

## Method notes (not errors, but must be reported)

* **M1 – Inconsistent CI definitions across RQs.** RQ1 uses 24 services, RQ2 uses 11 (15 patterns), RQ3 uses GitHub Actions only, while all three are described as "CI/CD changes".
* **M2 – Merge rate denominator** includes still-open PRs (merged = `merged_at` not null), not stated in the paper.
* **D9 – RQ3 comparison group contains human PRs (most consequential issue).** The runs CSV has an
  `author_type` column: 24,860 completed runs (5,233 PRs) are `human`, i.e. not AIDev-pop agent PRs.
  `rq_3.py` only checks agent commit files, so every human run is labelled "Did Not Modify CI YAML".
  Human runs succeed 86.7 % vs 71.0 % for agent runs, inflating the comparison group.

  | RQ3 overall | modified GHA | not modified | diff |
  |---|---|---|---|
  | published (agent + human runs) | 75.59 % | 74.87 % | +0.72 pp, χ² = 2.20, p = .138 |
  | agent PRs only, naive χ² | 75.59 % | 70.43 % | +5.16 pp, χ² = 100.8, p < 10⁻²³ |
  | agent only, GEE clustered by PR | | | OR = 1.07, p = .118 |
  | agent only, GEE by repo + agent FE | | | OR = 1.12, p = .040 |
  | agent only, PR level, first run | 73.65 % | 74.92 % | −1.27 pp, p = .237 |
  | agent only, success/(success+failure), GEE | 83.78 % | 76.65 % | OR = 1.07, p = .184 |

  The published "comparable" conclusion is reached for the wrong reason (population mixing); with the
  correct population the run-level gap is +5.2 pp but it is not robust to clustering. Per-agent
  first-run effects are large and opposite: OpenAI Codex −13.5 pp (58.4 vs 71.9 %, p < 10⁻¹³), Copilot
  +5.4 pp (p = .009). Per-agent rates in Table 3 are unaffected (human rows have no agent).
  Script: `uv run python -m cicd_rai.rq3_robust` → `results/replication/rq3_robustness.csv`.
* **M3 – Unit of analysis in RQ3 is the workflow run**, so PRs with many runs dominate (see D9 for the
  clustered and PR-level re-analysis). The package's own `rq3_first_run_rates.csv` (73.81 % vs 76.70 %) is
  not reported in the paper and also mixes in human PRs.
* **M4 – Yates' correction** is applied silently on 2×2 tables (scipy default); no multiple-comparison correction across 5 agents.
* **M5 – Snapshot truncation:** AIDev v3/v4 sub-resource tables are truncated at 30 items per PR (fixed in v5), which can hide later commits/reviews in long PRs.
* **M7 – CI evidence has expired on GitHub (verified 2026-11-10).** Run ids from the CSV dated Dec 2024 and
  Jul 2025 return HTTP 404 and their commits have no check-runs; runs from Dec 2025 still exist (consistent
  with a ~400-day retention). The whole cohort (PRs Dec 2024 – Jul 2025) is past that window, so the base
  package's CSV — final heads only, exactly one commit per PR — is the **only surviving CI record**.
  Per-commit / first-attempt CI outcomes cannot be recollected. (Responsible-AI point: the audit trail for
  agent-made CI/CD changes is ephemeral unless archived.)
* **M6 – Run collection script not released.** The workflow-runs CSV has 99,979 rows (paper: 99,976 collected); 99,930 are `completed`.
