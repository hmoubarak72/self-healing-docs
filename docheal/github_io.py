"""Phase 4.2 / 4.3 — GitHub integration via PyGithub.

* High-confidence fixes -> branch ``docheal/fix-<pr>`` + PR into the source PR's
  head branch (so docs land together with the code change). Optional auto-merge.
* Low-confidence flags + summary -> a single sticky comment on the source PR
  (updated in place on every run).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

from .report import MARKER

log = logging.getLogger(__name__)


@dataclass
class PRContext:
    repo: str
    number: int | None
    base_sha: str | None
    head_sha: str | None
    head_ref: str | None
    base_ref: str | None
    is_fork: bool = False

    @classmethod
    def from_env(cls) -> PRContext:
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        event = {}
        path = os.environ.get("GITHUB_EVENT_PATH")
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                event = json.load(f)
        pr = event.get("pull_request") or {}
        head_repo = ((pr.get("head") or {}).get("repo") or {}).get("full_name")
        return cls(
            repo=repo,
            number=pr.get("number"),
            base_sha=(pr.get("base") or {}).get("sha"),
            head_sha=(pr.get("head") or {}).get("sha") or os.environ.get("GITHUB_SHA"),
            head_ref=(pr.get("head") or {}).get("ref") or os.environ.get("GITHUB_HEAD_REF"),
            base_ref=(pr.get("base") or {}).get("ref") or os.environ.get("GITHUB_BASE_REF"),
            is_fork=bool(head_repo and repo and head_repo != repo),
        )


class GitHubClient:
    def __init__(self, token: str, repo: str):
        from github import Auth, Github

        self.gh = Github(auth=Auth.Token(token))
        self.repo = self.gh.get_repo(repo)

    # -- comments ---------------------------------------------------------------
    def upsert_comment(self, pr_number: int, body: str) -> str:
        issue = self.repo.get_issue(pr_number)
        for c in issue.get_comments():
            if MARKER in (c.body or ""):
                c.edit(body)
                return c.html_url
        return issue.create_comment(body).html_url

    # -- fix PR -----------------------------------------------------------------
    def open_fix_pr(self, *, files: dict[str, str], source_pr: int | None, target_branch: str, from_sha: str,
                    title: str, body: str, auto_merge: bool = False) -> str:
        branch = f"docheal/fix-{source_pr or from_sha[:7]}"
        ref_name = f"refs/heads/{branch}"
        try:
            ref = self.repo.get_git_ref(f"heads/{branch}")
            ref.edit(from_sha, force=True)
        except Exception:
            self.repo.create_git_ref(ref_name, from_sha)
        # One commit containing all changed files (Git Data API).
        base_commit = self.repo.get_git_commit(from_sha)
        from github import InputGitTreeElement

        elements = [InputGitTreeElement(path=p, mode="100644", type="blob", content=c) for p, c in files.items()]
        tree = self.repo.create_git_tree(elements, base_commit.tree)
        commit = self.repo.create_git_commit(
            f"docs: DocHeal auto-fix for stale sections{f' (#{source_pr})' if source_pr else ''}", tree,
            [base_commit])
        self.repo.get_git_ref(f"heads/{branch}").edit(commit.sha, force=True)

        existing = list(self.repo.get_pulls(state="open", head=f"{self.repo.owner.login}:{branch}"))
        if existing:
            pr = existing[0]
            pr.edit(title=title, body=body)
        else:
            pr = self.repo.create_pull(title=title, body=body, head=branch, base=target_branch)
        try:
            pr.add_to_labels("documentation", "docheal")
        except Exception:  # labels are best-effort
            pass
        if auto_merge:
            try:
                pr.merge(merge_method="squash", commit_title=title)
                log.info("Auto-merged %s", pr.html_url)
            except Exception as exc:
                log.warning("Auto-merge failed (%s); leaving PR open", exc)
        return pr.html_url


def set_outputs(**values: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        for k, v in values.items():
            delim = "EOF_DOCHEAL"
            f.write(f"{k}<<{delim}\n{v}\n{delim}\n")


def write_step_summary(markdown: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(markdown + "\n")
