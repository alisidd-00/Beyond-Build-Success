"""CI/CD file detection, copied verbatim from Ghaleb's replication package.

Source: external/ghaleb-replication/code/rq_1.py (commit 4ae0b54).
Kept byte-for-byte so the replication is faithful; known quirks are documented
in docs/discrepancy_log.md rather than silently fixed here.
"""

from __future__ import annotations

import re

# rq_1.py: 24 services, first match wins, re.IGNORECASE, re.search (unanchored).
CI_PATTERNS: dict[str, list[str]] = {
    "GitHub Actions": [r"\.github/workflows/.*\.ya?ml$"],
    "Travis CI": [r"\.travis\.ya?ml$"],
    "CircleCI": [r"\.circleci/config\.ya?ml$", r"circle\.ya?ml$"],
    "GitLab CI": [r"\.gitlab-ci\.ya?ml$"],
    "AppVeyor": [r"\.appveyor\.ya?ml$", r"^appveyor\.ya?ml$"],
    "Azure Pipelines": [r"azure-pipelines.*\.ya?ml$", r"\.azure-pipelines.*\.ya?ml$"],
    "Bitbucket Pipelines": [r"bitbucket-pipelines\.ya?ml$"],
    "Jenkins": [r"Jenkinsfile", r"jenkins\.ya?ml$", r"Jenkinsfile\.groovy$"],
    "Drone CI": [r"\.drone\.ya?ml$"],
    "Buildkite": [r"\.buildkite/.*\.ya?ml$", r"buildkite\.ya?ml$"],
    "Cirrus CI": [r"\.cirrus\.ya?ml$"],
    "Scrutinizer CI": [r"\.scrutinizer\.ya?ml$"],
    "Codeship": [r"codeship-services\.ya?ml$", r"codeship-steps\.ya?ml$"],
    "Semaphore": [r"\.semaphore/semaphore\.ya?ml$", r"\.semaphore/.*\.ya?ml$"],
    "Wercker": [r"wercker\.ya?ml$"],
    "Bitrise": [r"bitrise\.ya?ml$"],
    "Bamboo": [r"bamboo\.ya?ml$"],
    "GoCD": [r"\.gocd\.ya?ml$"],
    "Codemagic": [r"codemagic\.ya?ml$"],
    "Buddy": [r"buddy\.ya?ml$"],
    "TeamCity": [r"\.teamcity/.*\.ya?ml$"],
    "Concourse CI": [r"\.concourse\.ya?ml$", r"concourse\.ya?ml$"],
    "Codefresh": [r"codefresh\.ya?ml$"],
    "Shippable": [r"shippable\.ya?ml$"],
}

_COMPILED = [(svc, [re.compile(p, re.IGNORECASE) for p in pats]) for svc, pats in CI_PATTERNS.items()]
GHA_RE = re.compile(r"\.github/workflows/.*\.ya?ml$", re.IGNORECASE)


def identify_ci_service(filename: str | None) -> str | None:
    if not filename:
        return None
    for svc, pats in _COMPILED:
        if any(p.search(filename) for p in pats):
            return svc
    return None


def is_github_actions(filename: str | None) -> bool:
    return bool(filename) and GHA_RE.search(filename) is not None


def duckdb_ci_case(col: str = "filename") -> str:
    """SQL CASE expression equivalent to identify_ci_service (DuckDB regexp, case-insensitive)."""
    arms = []
    for svc, pats in CI_PATTERNS.items():
        cond = " OR ".join(f"regexp_matches({col}, '{p}', 'i')" for p in pats)
        arms.append(f"WHEN {cond} THEN '{svc}'")
    return "CASE " + " ".join(arms) + " ELSE NULL END"
