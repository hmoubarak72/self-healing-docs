import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SAMPLE = ROOT / "examples" / "sample_project"


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


@pytest.fixture
def sample_repo(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(SAMPLE, repo, ignore=shutil.ignore_patterns(".docheal", "__pycache__"))
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    return repo


def edit(repo, path, old, new):
    p = Path(repo) / path
    t = p.read_text()
    assert old in t, f"anchor not found: {old!r}"
    p.write_text(t.replace(old, new, 1))
