import json

from docheal.code_parser import parse_source
from docheal.doc_parser import parse_markdown
from docheal.llm import TODO_PREFIX, LLMEngine, RuleEngine, mentions, parse_json, structural_validation
from docheal.models import CodeChange


def change(old_src, new_src, name):
    o = {c.qualname: c for c in parse_source("m.py", old_src)}[name]
    n_all = {c.qualname: c for c in parse_source("m.py", new_src)}
    n = n_all.get(name) or next(iter(n_all.values()))
    return CodeChange(o.id, "signature_changed", o, n)


SEC = parse_markdown("d.md", "## Usage\n\nCall `connect(host, retries=3)` to connect.\nThe retries are logged.\n")[0]


def test_rule_engine_rename_param_autofix():
    ch = change("def connect(host, retries=3):\n    pass\n", "def connect(host, attempts=3):\n    pass\n", "connect")
    eng = RuleEngine()
    v = eng.verify(SEC, [ch])
    assert v.stale and v.confidence >= 0.8
    fix = eng.repair(SEC, [ch], v)
    assert "attempts=3" in fix.corrected and fix.complexity == "simple"
    assert "The retries are logged." in fix.corrected  # prose untouched
    assert eng.validate(SEC, fix.corrected, [ch], v).passed


def test_rule_engine_removed_feature_needs_review():
    old = parse_source("m.py", "def connect(host, retries=3):\n    pass\n")[0]
    ch = CodeChange(old.id, "removed", old, None)
    eng = RuleEngine()
    v = eng.verify(SEC, [ch])
    fix = eng.repair(SEC, [ch], v)
    assert v.stale and fix.complexity == "complex" and TODO_PREFIX in fix.corrected


def test_rule_engine_unrelated_change_is_accurate():
    ch = change("def connect(host, retries=3):\n    return 1\n", "def connect(host, retries=3):\n    return 2\n",
                "connect")
    assert not RuleEngine().verify(SEC, [ch]).stale


def test_plain_words_only_match_in_code():
    assert mentions("run `taskq worker`", "worker")
    assert not mentions("a pool of worker processes", "worker")
    assert mentions("set MAX_SIZE high", "MAX_SIZE")


def test_structural_validation_catches_rewrites():
    bad = "## Usage\n\nTotally different text.\nNothing else.\nMore.\nAnd more.\n"
    sec = parse_markdown("d.md", "## Usage\n\nline a\nline b\nline c\nline d\n")[0]
    assert not structural_validation(sec, bad, []).passed
    assert not structural_validation(sec, "## Changed heading\n\nline a", []).passed


class FakeClient:
    model = "fake-model"

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def complete(self, system, user):
        self.prompts.append((system, user))
        return self.replies.pop(0)


def test_llm_engine_flow_with_fake_client():
    ch = change("def connect(host, retries=3):\n    pass\n", "def connect(host, attempts=3):\n    pass\n", "connect")
    corrected = SEC.content.replace("retries=3", "attempts=3")
    client = FakeClient([
        json.dumps({"stale": True, "confidence": 0.93, "issues": ["param renamed"], "explanation": "renamed"}),
        "```json\n" + json.dumps({"corrected_section": corrected, "confidence": 0.9, "complexity": "simple",
                                  "summary": "renamed"}) + "\n```",
        json.dumps({"accurate": True, "preserved": True, "style_consistent": True, "score": 0.95, "notes": "ok"}),
    ])
    eng = LLMEngine(client)
    v = eng.verify(SEC, [ch])
    fix = eng.repair(SEC, [ch], v)
    val = eng.validate(SEC, fix.corrected, [ch], v)
    assert v.stale and fix.corrected == corrected and val.passed and val.score == 0.95
    assert "attempts" in client.prompts[0][1] and "OLD CODE" in client.prompts[0][1]


def test_llm_engine_falls_back_on_garbage():
    ch = change("def connect(host, retries=3):\n    pass\n", "def connect(host, attempts=3):\n    pass\n", "connect")
    v = LLMEngine(FakeClient(["not json at all"])).verify(SEC, [ch])
    assert v.stale  # rule engine fallback still catches it


def test_parse_json_variants():
    assert parse_json('noise {"a": 1} trailing') == {"a": 1}
