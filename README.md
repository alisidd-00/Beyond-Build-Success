# Beyond Build Success — Responsible AI for agent-generated CI/CD changes

AI630 group project (Khadija Hakim, M. Ali Siddique, Fatima Ghazanfer, Abdullah Qureshi).

**Replication + extension of** Ghaleb (2026), *When AI Agents Touch CI/CD Configurations:
Frequency and Success* (MSR 2026 Mining Challenge, arXiv:2601.17413).
Base replication package: <https://github.com/Taher-Ghaleb/AIAgentsCI_CD-MSR2026> (commit `4ae0b54`).

## Research questions

| | Question | Source |
|---|---|---|
| RQ1–RQ3 (replication) | How often do agents change CI/CD config, and how does that relate to merge rate and workflow-run success? | Ghaleb (2026) |
| RQ4 | Which GitHub Actions security risks do agent PRs **introduce or worsen** (vs. pre-existing ones)? | extension |
| RQ5 | Are introduced risks associated with CI outcome, merge decision, and recorded human review? Do risks change between the agent's first attempt and the final revision (after feedback)? | extension |
| RQ6 | Does a revision-aware, explainable review policy surface confirmed risks with fewer irrelevant alerts than scanning the current file? | extension |

## Data (pinned)

* **AIDev-pop**, Hugging Face `hao-li/AIDev` @ `4c65121fd48b495017d725014fe0e4cdb4b599e5`
  (2025-10-28, the "Oct 28, 2025 update" the base paper used; tables identical to tag `v3`).
  `main` is now v5 (7.6M PRs) — **do not** download from `main`.
  Known caveat: v3/v4 sub-resource tables (commits, reviews, comments) are truncated at 30 items per PR.
* `all_agentic_prs_ci_output_worklfows.csv` from the base package (GitHub Actions runs, with `commit_sha` per run).
* Workflow file contents at base / first-head / final-head revisions, fetched from GitHub (static analysis only; nothing is executed).

## Setup

```bash
uv sync                                               # Python deps + zizmor 1.32.0 + actionlint 1.7.12 (pinned in uv.lock)
git clone https://github.com/Taher-Ghaleb/AIAgentsCI_CD-MSR2026 external/ghaleb-replication
uv run python scripts/download_aidev.py               # ~590 MB, sha256-verified
```

## Pipeline

1. **Reproduce** — run Ghaleb's `rq_1..3.py` unchanged on the pinned data; then a clean DuckDB re-implementation; log every discrepancy.
2. **Align history** — for each GitHub Actions-touching PR: base → first agent head → final head; attach the CI runs for each `commit_sha` and the review events in between.
3. **Scan** — zizmor (offline) + actionlint on each workflow file at each revision; diff findings → *introduced / worsened / unchanged / fixed*.
4. **Label** — stratified sample of 150–200 PRs, two independent raters per PR, codebook in `docs/`, Cohen's κ.
5. **Evaluate & analyse** — baselines (current-file scan, security delta, proposed policy): precision / recall / F1, recall at a 20 % review budget; associations of risk with CI outcome, merge and review; repository- and time-based holdouts.

Ethics: static analysis only, secrets redacted, actors pseudonymised, missing evidence stays *unknown* (never imputed).

## Timeline (today = Tue 10 Nov 2026)

| Dates | Milestone | Output |
|---|---|---|
| 10–11 Nov | Setup + reproduce RQ1–3 | replication table + discrepancy log |
| 11–13 Nov | Revision retrieval + scanning on all GHA-touching PRs | `data/interim/findings.parquet`, pilot stats |
| **15 Nov** | **Short paper due** | progress paper (replication + pilot results) |
| 15–18 Nov | Manual labeling + review-trace extraction | labels, κ |
| 18–21 Nov | Evaluation + association analysis | final tables/figures |
| **22 Nov** | **Final presentation** | slides |
| **30 Nov** | **Final paper** (CORE A/A* template, written by the group) | paper |

## Layout

```
scripts/        one-off entry points (download, fetch revisions, scan)
src/cicd_rai/   reusable code (CI file patterns, revision alignment, finding diff, policy)
data/           raw + interim data (git-ignored)
results/        tables and figures used in the paper/slides
docs/           labeling codebook, discrepancy log, notes
external/       third-party code (git-ignored)
```
