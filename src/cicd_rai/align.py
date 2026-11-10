"""Attach CI outcomes and human-review evidence to every workflow revision.

Inputs
  data/interim/revision_manifest.parquet   revisions per (PR, workflow path)   [fetch_revisions.py]
  data/interim/cohort_pr_commits.parquet   ordered PR commits with dates       [fetch_revisions.py]
  data/interim/ci_runs_api.parquet         Actions runs per commit (API)       [fetch_revisions.py]
  external/.../all_agentic_prs_ci_output_worklfows.csv   Ghaleb's runs (final head only)
  AIDev-pop: pull_request, pr_reviews, pr_review_comments, pr_comments

Outputs
  data/interim/revision_evidence.parquet   one row per (PR, path, PR revision): CI outcome at that commit,
                                           feedback received before it
  data/interim/pr_evidence.parquet         one row per PR: outcome + oversight summary

Oversight is classified by actor, never stored by name (pseudonymisation):
  human_independent  user_type == 'User' and not the PR author
  self               the PR author (for Codex/Cursor/Claude Code PRs this is the human who ran the agent)
  bot                user_type == 'Bot' (e.g. automated reviewers)
Missing evidence stays missing: CI outcome is 'unknown' when the API could not be queried.

Usage:  uv run python -m cicd_rai.align
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "aidev"
INTERIM = ROOT / "data" / "interim"
RUNS_CSV = ROOT / "external" / "ghaleb-replication" / "data" / "all_agentic_prs_ci_output_worklfows.csv"

FAILURE = ("failure", "timed_out", "startup_failure")  # same failure set as Ghaleb's rq_3.py


def build(con: duckdb.DuckDBPyConnection) -> tuple[pd.DataFrame, pd.DataFrame]:
    con.sql(f"CREATE VIEW manifest AS SELECT * FROM '{INTERIM / 'revision_manifest.parquet'}'")
    con.sql(f"""CREATE VIEW commits AS SELECT pr_id, sha, "order",
                  try_cast(committer_date AS TIMESTAMPTZ) AS committed_at
                FROM '{INTERIM / 'cohort_pr_commits.parquet'}'""")
    con.sql(f"CREATE VIEW pr AS SELECT * FROM '{RAW / 'pull_request.parquet'}'")
    con.sql("CREATE TABLE cohort AS SELECT DISTINCT pr_id FROM manifest")

    # ---- oversight events (reviews, inline review comments, conversation comments) ---------------
    con.sql(f"""
      CREATE TABLE events AS
      WITH r AS (
        SELECT r.pr_id, 'review' AS kind, r.state, NULL AS path,
               try_cast(r.submitted_at AS TIMESTAMPTZ) AS at, r.user, r.user_type
        FROM '{RAW / 'pr_reviews.parquet'}' r WHERE r.pr_id IN (SELECT pr_id FROM cohort)),
      rc AS (
        SELECT rv.pr_id, 'review_comment' AS kind, NULL AS state, c.path,
               try_cast(c.created_at AS TIMESTAMPTZ) AS at, c.user, c.user_type
        FROM '{RAW / 'pr_review_comments.parquet'}' c
        JOIN '{RAW / 'pr_reviews.parquet'}' rv ON rv.id = c.pull_request_review_id
        WHERE rv.pr_id IN (SELECT pr_id FROM cohort)),
      ic AS (
        SELECT c.pr_id, 'comment' AS kind, NULL AS state, NULL AS path,
               try_cast(c.created_at AS TIMESTAMPTZ) AS at, c.user, c.user_type
        FROM '{RAW / 'pr_comments.parquet'}' c WHERE c.pr_id IN (SELECT pr_id FROM cohort))
      SELECT e.pr_id, e.kind, e.state, e.path, e.at,
             -- 'self' first: for Devin/Copilot the PR author is the agent's own bot account,
             -- so its comments are the agent talking, not independent review
             CASE WHEN e.user = p.user THEN 'self'
                  WHEN e.user_type = 'Bot' THEN 'bot'
                  ELSE 'human_independent' END AS actor
      FROM (SELECT * FROM r UNION ALL SELECT * FROM rc UNION ALL SELECT * FROM ic) e
      JOIN pr p ON p.id = e.pr_id
    """)

    # ---- CI outcome per commit -------------------------------------------------------------------
    ci_api = INTERIM / "ci_runs_api.parquet"
    has_runs = ci_api.exists() and "run_id" in pd.read_parquet(ci_api).columns
    if has_runs:
        con.sql(f"""
          CREATE TABLE ci AS
          SELECT repo, sha, any_value(api_status) AS api_status,
                 count(run_id) AS n_runs,
                 count(*) FILTER (run_conclusion = 'success') AS n_success,
                 count(*) FILTER (run_conclusion IN {FAILURE}) AS n_failure
          FROM '{ci_api}' GROUP BY ALL""")
    else:
        con.sql("CREATE TABLE ci (repo VARCHAR, sha VARCHAR, api_status VARCHAR, n_runs BIGINT, "
                "n_success BIGINT, n_failure BIGINT)")
    con.sql(f"""
      CREATE TABLE ci_csv AS
      SELECT commit_sha AS sha, count(*) FILTER (status = 'completed') AS csv_n_runs,
             count(*) FILTER (status = 'completed' AND conclusion = 'success') AS csv_n_success,
             count(*) FILTER (status = 'completed' AND conclusion IN {FAILURE}) AS csv_n_failure
      FROM read_csv('{RUNS_CSV}', all_varchar = true) GROUP BY 1""")

    # ---- per revision ----------------------------------------------------------------------------
    rev = con.sql("""
      WITH m AS (
        SELECT m.*, c.committed_at FROM manifest m
        LEFT JOIN commits c ON c.pr_id = m.pr_id AND c.sha = m.sha
        WHERE m.role <> 'base' AND m.sha IS NOT NULL),
      first_touch AS (SELECT pr_id, path, min(committed_at) AS t1 FROM m GROUP BY ALL)
      SELECT m.pr_id, m.agent, m.path, m.role, m.sha, m.committed_at,
             -- Ghaleb's CSV first (collected while runs still existed; final heads only), then the API
             -- for recent commits. Absence from both is 'unknown', never 'no_runs'.
             CASE WHEN cc.csv_n_runs > 0 THEN
                       CASE WHEN cc.csv_n_failure > 0 THEN 'failure'
                            WHEN cc.csv_n_success = cc.csv_n_runs THEN 'success' ELSE 'other' END
                  WHEN ci.api_status = 'ok' AND ci.n_runs > 0 THEN
                       CASE WHEN ci.n_failure > 0 THEN 'failure'
                            WHEN ci.n_success = ci.n_runs THEN 'success' ELSE 'other' END
                  ELSE 'unknown' END AS ci_outcome,
             CASE WHEN cc.csv_n_runs > 0 THEN 'ghaleb_csv'
                  WHEN ci.api_status = 'ok' AND ci.n_runs > 0 THEN 'github_api' END AS ci_source,
             ci.n_runs, ci.n_success, ci.n_failure,
             cc.csv_n_runs, cc.csv_n_success, cc.csv_n_failure,
             -- feedback that arrived before this revision was committed (and after the first attempt)
             (SELECT count(*) FROM events e WHERE e.pr_id = m.pr_id AND e.actor = 'human_independent'
                AND e.at > ft.t1 AND e.at < m.committed_at) AS human_feedback_before,
             (SELECT count(*) FROM events e WHERE e.pr_id = m.pr_id AND e.actor = 'human_independent'
                AND e.kind = 'review_comment' AND e.path = m.path
                AND e.at > ft.t1 AND e.at < m.committed_at) AS human_comments_on_file_before,
             (SELECT count(*) FROM events e WHERE e.pr_id = m.pr_id AND e.actor = 'bot'
                AND e.at > ft.t1 AND e.at < m.committed_at) AS bot_feedback_before
      FROM m
      JOIN first_touch ft USING (pr_id, path)
      LEFT JOIN ci ON ci.repo = m.repo AND ci.sha = m.sha
      LEFT JOIN ci_csv cc ON cc.sha = m.sha
      ORDER BY m.pr_id, m.path, m."order"
    """).df()

    # ---- per PR ----------------------------------------------------------------------------------
    prs = con.sql("""
      SELECT p.id AS pr_id, p.agent, p.state,
             p.merged_at IS NOT NULL AS merged,
             CASE WHEN p.merged_at IS NOT NULL THEN 'merged'
                  WHEN p.state = 'closed' THEN 'closed_unmerged' ELSE 'open' END AS outcome,
             try_cast(p.created_at AS TIMESTAMPTZ) AS created_at,
             try_cast(p.merged_at AS TIMESTAMPTZ) AS merged_at,
             (SELECT count(*) FROM commits c WHERE c.pr_id = p.id) AS n_commits_api,
             count(*) FILTER (e.actor = 'human_independent' AND e.kind = 'review') AS human_reviews,
             count(*) FILTER (e.actor = 'human_independent' AND e.state = 'APPROVED') AS human_approvals,
             count(*) FILTER (e.actor = 'human_independent' AND e.state = 'CHANGES_REQUESTED') AS human_changes_requested,
             count(*) FILTER (e.actor = 'human_independent' AND e.kind = 'review_comment'
                              AND regexp_matches(e.path, '\\.github/workflows/')) AS human_comments_on_workflows,
             count(*) FILTER (e.actor = 'human_independent' AND e.kind = 'comment') AS human_conversation_comments,
             count(*) FILTER (e.actor = 'self') AS self_events,
             count(*) FILTER (e.actor = 'bot' AND e.kind = 'review') AS bot_reviews,
             count(*) FILTER (e.actor = 'bot' AND e.kind = 'review_comment'
                              AND regexp_matches(e.path, '\\.github/workflows/')) AS bot_comments_on_workflows,
             count(e.pr_id) FILTER (e.actor = 'human_independent') > 0 AS any_independent_human_oversight
      FROM pr p LEFT JOIN events e ON e.pr_id = p.id
      WHERE p.id IN (SELECT pr_id FROM cohort)
      GROUP BY ALL ORDER BY p.id
    """).df()
    return rev, prs


def main() -> None:
    con = duckdb.connect()
    rev, prs = build(con)
    rev.to_parquet(INTERIM / "revision_evidence.parquet", index=False)
    prs.to_parquet(INTERIM / "pr_evidence.parquet", index=False)
    print(f"revision_evidence: {len(rev)} rows | pr_evidence: {len(prs)} PRs")
    print(rev.groupby(["role", "ci_outcome"]).size().unstack(fill_value=0).to_string())
    final = rev[rev.role.str.endswith("final") & rev.n_runs.notna() & rev.csv_n_runs.notna()]
    if len(final):
        agree = (final.n_failure > 0) == (final.csv_n_failure > 0)
        print(f"API vs Ghaleb CSV, final head 'any failure' agreement: {agree.mean():.1%} (n={len(final)})")
    print(prs[["outcome", "any_independent_human_oversight"]].value_counts().to_string())


if __name__ == "__main__":
    main()
