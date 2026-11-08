# Real-repository benchmark — encode/httpx@b5addb6 — engine `rules`

Scenarios were written before any tuning. The first (untuned) run is archived in `benchmarks/results/httpx_rules_v0_untuned.md`; two generic fixes followed (evidence-weighted suspect ranking, literal-level default diffs).

**Precision 1.0 · Recall 0.889 · F1 0.941** (TP 8, FP 0, FN 1; 8 auto-fix, 0 review)

| Scenario | Truly stale | TP | FP | FN | Suspects checked |
|---|---|---|---|---|---|
| rename follow_redirects -> allow_redirects (public API) | 3 | 3 | 0 | 0 | 60 |
| rename Response.iter_bytes -> iter_raw_bytes | 4 | 4 | 0 | 0 | 4 |
| default keep-alive pool 20 -> 10 | 1 | 1 | 0 | 0 | 2 |
| default timeout 5s -> 10s | 1 | 0 | 0 | 1 | 1 |
| default max redirects 20 -> 30 | 0 | 0 | 0 | 0 | 0 |
| negative: comment-only edit | 0 | 0 | 0 | 0 | 0 |
| negative: private helper refactor | 0 | 0 | 0 | 0 | 0 |

## Misses

- FN — default timeout 5s -> 10s: `docs/advanced/timeouts.md#preamble`
