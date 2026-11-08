import sys
import types

from docheal.github_io import GitHubClient, PRContext
from docheal.report import MARKER


class Obj(types.SimpleNamespace):
    pass


class FakeRepo:
    def __init__(self):
        self.calls = []
        self.owner = Obj(login="octo")
        self.comments = [Obj(body="hi", edit=lambda b: None)]

    def get_git_ref(self, name):
        self.calls.append(("get_ref", name))
        if not any(c[0] == "create_ref" for c in self.calls):
            raise RuntimeError("404")
        return Obj(edit=lambda sha, force=False: self.calls.append(("move_ref", sha)))

    def create_git_ref(self, ref, sha):
        self.calls.append(("create_ref", ref, sha))

    def get_git_commit(self, sha):
        return Obj(sha=sha, tree=Obj(sha="tree0"))

    def create_git_tree(self, elements, base):
        self.calls.append(("tree", [e.path for e in elements]))
        return Obj(sha="tree1")

    def create_git_commit(self, msg, tree, parents):
        self.calls.append(("commit", msg))
        return Obj(sha="c1")

    def get_pulls(self, state, head):
        self.calls.append(("pulls", head))
        return []

    def create_pull(self, title, body, head, base):
        self.calls.append(("create_pull", head, base))
        return Obj(html_url="https://github.com/o/r/pull/43", add_to_labels=lambda *a: None,
                   merge=lambda **k: self.calls.append(("merge", k)))

    def get_issue(self, n):
        repo = self

        class Issue:
            def get_comments(self):
                return repo.comments

            def create_comment(self, body):
                repo.calls.append(("comment", body[:30]))
                return Obj(html_url="https://github.com/o/r/pull/7#c1")
        return Issue()


def make_client():
    c = GitHubClient.__new__(GitHubClient)
    c.repo = FakeRepo()
    return c


def test_open_fix_pr_flow(monkeypatch):
    class Elem:
        def __init__(self, path, mode, type, content):
            self.path = path
    fake_github = types.ModuleType("github")
    fake_github.InputGitTreeElement = Elem
    monkeypatch.setitem(sys.modules, "github", fake_github)
    c = make_client()
    url = c.open_fix_pr(files={"docs/a.md": "x"}, source_pr=7, target_branch="feature", from_sha="abc1234",
                        title="t", body="b", auto_merge=True)
    kinds = [k[0] for k in c.repo.calls]
    assert url.endswith("/pull/43")
    assert ("create_ref", "refs/heads/docheal/fix-7", "abc1234") in c.repo.calls
    assert ("create_pull", "docheal/fix-7", "feature") in c.repo.calls
    assert kinds.index("tree") < kinds.index("commit") < kinds.index("create_pull") < kinds.index("merge")


def test_comment_is_upserted():
    c = make_client()
    assert c.upsert_comment(7, MARKER + "\nbody").endswith("#c1")
    edited = []
    c.repo.comments = [Obj(body=MARKER + " old", edit=edited.append, html_url="u")]
    assert c.upsert_comment(7, MARKER + " new") == "u" and edited == [MARKER + " new"]


def test_pr_context_detects_forks(tmp_path, monkeypatch):
    ev = tmp_path / "e.json"
    ev.write_text('{"pull_request": {"number": 3, "base": {"sha": "b", "ref": "main"},'
                  ' "head": {"sha": "h", "ref": "f", "repo": {"full_name": "fork/r"}}}}')
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(ev))
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    ctx = PRContext.from_env()
    assert ctx.number == 3 and ctx.is_fork and ctx.head_ref == "f"
