from docheal.doc_parser import extract_references, parse_markdown, replace_section

MD = """Intro text.

# Guide

## Configuration

Set `MAX_RETRIES` or pass `retries=3` to `Client.connect()`.

```bash
app serve --port 8000
```

### Environment Variables

`APP_REGION` selects the region. `GET /items/{item_id}` works.

```python
# a heading inside code is not a heading
```

## Other
text
"""


def test_heading_paths_and_spans():
    secs = {s.heading_path: s for s in parse_markdown("docs/guide.md", MD)}
    assert set(secs) == {"(preamble)", "Guide", "Guide > Configuration",
                         "Guide > Configuration > Environment Variables", "Guide > Other"}
    env = secs["Guide > Configuration > Environment Variables"]
    assert env.id == "docs/guide.md#guide-configuration-environment-variables"
    assert "# a heading inside code" in env.content


def test_references():
    refs = set(extract_references(MD))
    for r in ["MAX_RETRIES", "retries", "Client.connect", "connect", "--port", "APP_REGION", "/items/{item_id}"]:
        assert r in refs, r


def test_replace_section_roundtrip():
    secs = {s.heading_path: s for s in parse_markdown("g.md", MD)}
    s = secs["Guide > Other"]
    out = replace_section(MD, s, "## Other\nnew text")
    assert out.endswith("## Other\nnew text\n") and out.startswith("Intro text.")


def test_replace_section_keeps_separator_blank_line():
    md = "## A\none\n\n## B\ntwo\n"
    a = parse_markdown("x.md", md)[0]
    assert replace_section(md, a, "## A\nONE") == "## A\nONE\n\n## B\ntwo\n"
