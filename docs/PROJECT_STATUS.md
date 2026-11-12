# Project status — Beyond Build Success

**Responsible AI for agent-generated CI/CD changes** · AI630 group project
Khadija Hakim · M. Ali Siddique · Fatima Ghazanfer · Abdullah Qureshi
Status as of **Tuesday 10 November 2026**

---

## In one paragraph

We reproduced the base paper (Ghaleb 2026, MSR) almost exactly and found errors in it, including one that changes its main build-success conclusion. We then built and ran the full extension pipeline on every agent PR that touches a GitHub Actions workflow (2,318 PRs, 95.6 % recovered). It shows that about half of these PRs introduce at least one security finding, that CI does not catch them, and that human review is not aimed at them. Our introduced-only view cuts alerts by 84 %. What remains is the manual labeling and the review-policy evaluation (RQ6). After that come the presentation and the final paper.

---

## 1. Deadlines

| Deliverable | Due | Status |
|---|---|---|
| Idea presentation | 24 Oct | submitted |
| Literature review (individual) | 30 Oct | submitted |
| Refined idea presentation | 31 Oct | submitted |
| **Short paper** (progress + preliminary results) | **Sun 15 Nov** | data and results ready; **writing not started** |
| Final presentation | Sun 22 Nov | — |
| Final paper (CORE A/A* conference template) | Mon 30 Nov | — |

> Course rule: AI tools must not write the literature review or the final paper. The code, data and numbers below are ready to use; the paper text must be our own.

---

## 2. What we have done

### 2.1 Pinned the exact dataset the base paper used
- **What:** The paper used the "Oct 28, 2025" AIDev-pop snapshot. Hugging Face has since moved to v5 (7.6 M PRs), so we pinned commit `4c65121f`. It has the same 33,596 PRs and 711,923 file-change rows as the paper. Downloads are checksum-verified.
- **Why it matters:** Without pinning, nobody, including the paper's own author, could reproduce these numbers today. Reproducibility is part of the final-paper rubric.

### 2.2 Replicated RQ1–RQ3: 65 of 72 published values reproduced exactly
We wrote an independent re-implementation (DuckDB) that reuses the author's exact file-matching rules. Every per-agent rate in all three RQs matches. Our numbers also match the author's own committed result files row for row, so the 7 differences are problems in the paper, not in our code.

| # | Discrepancy | Paper | Correct value |
|---|---|---|---|
| D1–D4 | Repos / PRs with YAML changes, per-project stats, "one-and-done" share | 1,605 repos, 8,031 PRs, 47 % | 936 repos, 3,995 PRs, 51.3 % (the author's own script output) |
| D5 | RQ2 overall χ² | 12.95 | 17.42 (can't come from the paper's own counts) |
| D6 | Codex χ² in Table 2 | 2.91 | 22.91 (typo) |
| D7 | Copilot merge advantage | 15.63 pp | 17.75 pp (paper contradicts its own table) |
| D8 | "CI/CD files are 3.25 % of agent changes" | 3.25 % | that is the YAML share; CI/CD is 1.10 % |

Full details: [`docs/discrepancy_log.md`](discrepancy_log.md).

### 2.3 The base paper's build-success finding (RQ3) is wrong
**This is our strongest replication result.** The paper says CI-changing agent PRs have "comparable" build success (75.59 % vs 74.87 %). But its "did not change CI" group contains **24,860 runs from 5,233 *human-written* PRs**, which succeed far more often (86.7 % vs 71.0 %). The script can't check human PRs for CI changes, so it puts them all in the comparison group.

| Analysis | Changed CI | Did not | Result |
|---|---|---|---|
| Published (agent and human PRs mixed) | 75.59 % | 74.87 % | +0.72 pp, p = .138 |
| Agent PRs only (correct population) | 75.59 % | 70.43 % | +5.16 pp |
| ↳ accounting for runs grouped within PRs | | | not significant (p = .118) |
| ↳ one observation per PR (first run) | 73.65 % | 74.92 % | −1.27 pp, p = .237 |
| ↳ OpenAI Codex, first run | 58.40 % | 71.93 % | **−13.5 pp, p < 10⁻¹³** |
| ↳ Copilot, first run | | | +5.4 pp, p = .009 |

**Why it matters:** the "comparable" conclusion was reached for the wrong reason, and the real story is agent-specific. Codex's CI changes fail much more often on the first run.

### 2.4 Found that the CI evidence has been deleted from GitHub
- **What:** We tried to fetch CI results for each agent commit. Runs from December 2024 and July 2025 now return "not found", while December 2025 runs still exist, consistent with a retention window of about 400 days. Every cohort PR is from December 2024 to July 2025.
- **So:** the base paper's CSV is now the **only surviving record** of these PRs' CI results, and it covers only each PR's final commit.
- **Why it matters (Responsible AI):** the audit trail for agent-made CI/CD changes disappears unless someone archives it. That is an accountability finding in its own right.

### 2.5 Built and ran the extension pipeline on the full cohort
1. **Fetch.** For each of the 2,318 PRs that touch `.github/workflows/`, we got every version of each workflow file: before the PR, after each agent commit, and at the end. We used plain `git`, so no GitHub token is needed. This matched the GitHub API exactly on a test set.
   - Recovered: **2,215 PRs (95.6 %)** across 640 repos, **13,549 file versions**.
   - Not recovered: 82 (repo deleted, made private, or PR ref gone) and 21 (history rewritten). These are recorded as unavailable, not dropped.
2. **Scan.** Every version was scanned offline with **zizmor** (security) and **actionlint** (correctness). That gave **254,635 findings**, with no scanner failures; 176 file versions are invalid YAML.
3. **Diff.** Findings are matched across versions by rule and location in the YAML structure, not by line number. That separates **introduced** from **pre-existing** findings even when lines shift.
4. **Align.** Each version is linked to its CI result and to the review activity before it. Reviewers are classed as independent human, the PR author ("self"), or bot. For Codex PRs the author is the human who launched the agent, so their 175 self-reviews are not counted as independent oversight. Usernames are never stored.

### 2.6 Extension results (preliminary)

**About half of agent PRs that touch workflows introduce at least one security finding.**

| Agent | PRs | Any default finding | High severity | High, excluding tag-pinning |
|---|---|---|---|---|
| Copilot | 534 | 59.0 % | 52.1 % | 14.8 % |
| Devin | 519 | 57.0 % | 46.6 % | 17.1 % |
| Cursor | 118 | 53.4 % | 46.6 % | 10.2 % |
| Claude Code | 57 | 50.9 % | 45.6 % | 15.8 % |
| OpenAI Codex | 986 | 42.7 % | 38.9 % | 9.4 % |
| **All** | **2,214** | **50.8 %** | **44.5 %** | **12.7 %** |

"Default" means what zizmor reports with its standard settings. The most common finding by far is an action pinned to a tag instead of a commit hash (923 PRs); zizmor rates it High, but many maintainers accept it, so we also report results without it. Other common findings: persisted checkout credentials (658 PRs), overly broad token permissions (423), secrets used outside a protected environment (275), High-severity template injection (99), cache poisoning (83).

**Key results**

| Finding | Evidence | Why it matters |
|---|---|---|
| **Showing only introduced findings cuts alerts by 84 %** | Whole-file scan: 97,244 alerts. Introduced only: 15,768. Median per PR: 22 → 1. | Supports the premise of our review policy (RQ6): reviewers see what the agent changed, not years of old warnings. |
| **CI doesn't catch these risks** | Controlling for agent, PR size and repo, risky PRs don't fail CI more often: OR 0.71 (95 % CI 0.48–1.03). | Direct evidence for our title: passing CI is not enough. |
| **Human review isn't aimed at risky PRs** | Raw data suggests risky PRs get more review and fewer merges, but after controls both vanish (OR ≈ 1.0, p > 0.5). | Reviewers respond to PR size, not to the security change itself. |
| **Much oversight is missing** | 866 of 1,574 merged PRs (55 %) have no review or comment from anyone except the author and bots. | Human-oversight gap (Responsible AI principle). |
| **Feedback is followed by fixes, but not targeted ones** | With independent human feedback: 19 % of risky first attempts later fixed (7 % without), but 20 % also gained new risks (6 % without). | Feedback goes with more revision overall, not specifically with security fixes. |

---

## 3. How this maps to our Responsible AI framing

| Principle (from our slides) | What we can now show |
|---|---|
| **Safety** | Half of workflow-touching agent PRs introduce findings; CI does not catch them. |
| **Transparency** | Introduced-only, revision-aware findings, each with a rule and location, instead of a flood of whole-file warnings. |
| **Accountability** | CI evidence for agent changes expires after about 400 days; we preserved what survives. Base-paper errors documented. |
| **Human oversight** | 55 % of merged PRs have no independent review; review is not aimed at risky changes; self-review separated from independent review. |

---

## 4. Limitations (must be stated in the paper)

- **Findings come from static analysis and are not confirmed vulnerabilities.** Precision is unknown until we label a sample (section 6).
- **The associations are observational, not causal.** We control for agent, size and repository, but not everything.
- **CI outcome exists only for the final commit**, and is unknown for 640 PRs (expired data, item M7).
- **Snapshot limits:** the AIDev v3 tables cap commits, reviews and comments at 30 per PR, and 67 cohort PRs hit that cap. Our git fetch recovers their full commit lists, but reviews may be incomplete.
- **Scope:** only GitHub Actions (96.8 % of agent CI/CD changes) and only public repositories.
- **Secrets check not finished:** the fetched workflow files have not been checked for hard-coded credentials, so keep the data within the group.

---

## 5. Where we stand against our proposal

| Proposal item (refined slides) | Status |
|---|---|
| W1 Reproduce: RQ1–RQ3, pinned versions | ✅ Done (65/72 exact; 9 issues documented) |
| W2 Retrieve + scan: zizmor + actionlint, history aligned | ✅ Done for the full cohort (not just the 150–200 PR pilot we proposed) |
| Revision-level evidence: new vs pre-existing risks | ✅ Done |
| Changes after human feedback | ✅ First version done (section 2.6) |
| Associations of risk with CI and review | ✅ Done, with controls |
| Missing data treated as "unknown" | ✅ Built into every step |
| W3 Label + review: validated labels, inter-rater agreement | ❌ **Not started**, next priority |
| Explainable review policy (routine / human security review / insufficient evidence) | ❌ Not implemented |
| Baselines compared on precision/recall/F1 and recall at a 20 % review budget | ❌ Needs labels and the policy |
| Risk disclosure in PR descriptions | ❌ Not started |
| Holdouts by repository and by time | ❌ Not started |
| Secrets redaction / pseudonymisation | 🟡 Usernames never stored; secrets check of fetched files not finished |
| GitHub repo for the team | 🟡 Blocked on GitHub sign-in (2FA); code is committed locally |

**In short:** the data and analysis side is ahead of plan. The human-labeling and policy-evaluation side, which is needed for RQ6 and full rubric marks, still has to be done.

---

## 6. Next steps

**By Sun 15 Nov: short paper** (written by us)
- Include: replication table and discrepancies (§2.2), the RQ3 correction (§2.3), expired CI evidence (§2.4), the pipeline (§2.5), and preliminary extension results (§2.6) with limitations (§4).
- Remaining work section: labeling, review policy, baseline evaluation, holdouts.
- Every number above is in `results/replication/*.csv` and `results/extension/*.csv`.

**15–18 Nov: manual labeling**
- Stratified sample of about 200 PRs (by agent and finding tier, including some with no findings to measure recall).
- Write a labeling guide: is each introduced finding a real risk, a policy-only issue, or a false positive?
- Two independent raters per PR; report Cohen's κ.

**18–21 Nov: review policy and evaluation**
- Implement the explainable policy: route each PR to *routine checks*, *human security review* or *insufficient evidence*, with a reason for each.
- Compare three approaches: whole-file scan, introduced-only, and our policy. Measure precision/recall/F1 and recall at a 20 % review budget.
- Optional if time allows: risk disclosure in PR descriptions; repository and time holdouts.

**22 Nov: final presentation.** **30 Nov: final paper.** Still to decide: which conference template (check the CORE ranking; MSR or FSE are natural fits).

**Suggested split** (to agree as a group): Ali runs the pipeline and analysis · Khadija writes up the replication · Fatima writes the labeling guide and runs labeling logistics · Abdullah does the statistics, figures and slides · all four label.

---

## 7. Where everything is

| What | Where |
|---|---|
| Plan, setup, how to run | [`README.md`](../README.md) |
| Replication discrepancies (D1–D9, M1–M7) | [`docs/discrepancy_log.md`](discrepancy_log.md) |
| Replication results | `results/replication/` (`comparison_with_paper.csv`, `rq3_robustness.csv`, …) |
| Extension results | `results/extension/` (`introduced_by_agent.csv`, `introduced_rules.csv`, `alert_volume.csv`, `associations*.csv`, `first_attempt_vs_revision.csv`, `coverage.csv`) |
| Workflow file versions, findings, evidence | `data/interim/` (not in git; shared separately) |
| Pinned raw dataset | `data/raw/aidev/` (not in git; `scripts/download_aidev.py` re-downloads it) |
| Base paper's CI-run CSV (only surviving copy of that CI data) | `external/ghaleb-replication/data/` |

**Re-running everything:**
```bash
uv sync
uv run python scripts/download_aidev.py          # pinned AIDev-pop, ~590 MB
git clone https://github.com/Taher-Ghaleb/AIAgentsCI_CD-MSR2026 external/ghaleb-replication
uv run python -m cicd_rai.replicate              # RQ1–RQ3 vs paper
uv run python -m cicd_rai.rq3_robust             # RQ3 correction
uv run python scripts/fetch_revisions_git.py     # workflow versions (~30 min, no token)
uv run python -m cicd_rai.scan                   # zizmor + actionlint (~9 min)
uv run python -m cicd_rai.align                  # CI + review evidence
uv run python -m cicd_rai.summarize              # extension tables
```
