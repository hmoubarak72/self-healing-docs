# Real-repository benchmark — encode/httpx@b5addb6 — engine `rules`

Held-out scenarios — first run, before any tuning.

**Precision 1.0 · Recall 0.444 · F1 0.615** (TP 4, FP 0, FN 5; 4 auto-fix, 0 review)

| Scenario | Truly stale | TP | FP | FN | Suspects checked |
|---|---|---|---|---|---|
| rename follow_redirects -> allow_redirects (public API) | 3 | 0 | 0 | 3 | 60 |
| rename Response.iter_bytes -> iter_raw_bytes | 4 | 4 | 0 | 0 | 4 |
| default keep-alive pool 20 -> 10 | 1 | 0 | 0 | 1 | 2 |
| default timeout 5s -> 10s | 1 | 0 | 0 | 1 | 1 |
| default max redirects 20 -> 30 | 0 | 0 | 0 | 0 | 0 |
| negative: comment-only edit | 0 | 0 | 0 | 0 | 0 |
| negative: private helper refactor | 0 | 0 | 0 | 0 | 0 |

## Misses

- FN — rename follow_redirects -> allow_redirects (public API): `docs/compatibility.md#requests-compatibility-guide-redirects`
- FN — rename follow_redirects -> allow_redirects (public API): `docs/compatibility.md#requests-compatibility-guide-request-instantiation`
- FN — rename follow_redirects -> allow_redirects (public API): `docs/quickstart.md#quickstart-redirection-and-history`
- FN — default keep-alive pool 20 -> 10: `docs/advanced/resource-limits.md#preamble`
- FN — default timeout 5s -> 10s: `docs/advanced/timeouts.md#preamble`
