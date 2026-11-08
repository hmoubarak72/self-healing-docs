from docheal.code_parser import is_test_file, parse_source

SRC = '''
import os
import click
from fastapi import FastAPI

TIMEOUT = 30
REGION = os.getenv("APP_REGION", "us-east-1")
app = FastAPI()


def public(a: int, b: str = "x", *, flag: bool = False) -> int:
    """Doc."""
    return a


def _private():
    return 1


@app.get("/items/{item_id}")
def read_item(item_id: int):
    return item_id


@click.command("serve")
@click.option("--port", default=8000)
def serve(port):
    pass


class Settings:
    host: str = "0.0.0.0"
    workers: int = 2

    def reload(self, hard: bool = False):
        pass
'''


def chunks():
    return {c.qualname: c for c in parse_source("pkg/mod.py", SRC)}


def test_extracts_semantic_units():
    c = chunks()
    assert c["public"].kind == "function"
    assert [p["name"] for p in c["public"].params] == ["a", "b", "flag"]
    assert c["public"].params[1]["default"] == "'x'"
    assert "_private" not in c
    assert c["read_item"].kind == "endpoint" and c["read_item"].extra["route"] == "/items/{item_id}"
    assert c["serve"].kind == "cli_command" and c["serve"].extra == {"command": "serve", "options": ["--port"]}
    assert c["Settings"].kind == "config"
    assert c["Settings.reload"].kind == "method"
    assert c["TIMEOUT"].kind == "config"
    assert c["env:APP_REGION"].extra["env_var"] == "APP_REGION"
    assert c["public"].id == "pkg/mod.py::public"


def test_hashes_ignore_comments_whitespace_and_docstrings():
    base = chunks()["public"]
    cosmetic = SRC.replace("    return a", "    # explain\n    return   a")
    doc_only = SRC.replace('"""Doc."""', '"""Better doc."""')
    behavior = SRC.replace("    return a\n", "    return a + 1\n", 1)
    sig = SRC.replace('b: str = "x"', 'b: str = "y"')
    c1 = {c.qualname: c for c in parse_source("pkg/mod.py", cosmetic)}["public"]
    c2 = {c.qualname: c for c in parse_source("pkg/mod.py", doc_only)}["public"]
    c3 = {c.qualname: c for c in parse_source("pkg/mod.py", behavior)}["public"]
    c4 = {c.qualname: c for c in parse_source("pkg/mod.py", sig)}["public"]
    assert c1.behavior_hash == base.behavior_hash and c1.signature_hash == base.signature_hash
    assert c2.behavior_hash == base.behavior_hash
    assert c3.behavior_hash != base.behavior_hash and c3.signature_hash == base.signature_hash
    assert c4.signature_hash != base.signature_hash


def test_syntax_error_is_tolerated():
    assert parse_source("x.py", "def broken(:\n") == []


def test_test_file_detection():
    assert is_test_file("tests/test_a.py") and is_test_file("pkg/a_test.py") and is_test_file("conftest.py")
    assert not is_test_file("pkg/testing_utils.py")
