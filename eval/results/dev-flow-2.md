# Query path evaluation run: dev-flow-2

108 questions from `eval/dev.jsonl`, through app/query_flow.py as /query runs it, one at a time. Statuses: answered 90, refused 18. 324 s in all.

## Where the questions went

A path missing: 2; a path extra: 33; decided by the keyword rule: 4. A path cancelled by the route: 24 questions (documents 14, data 10).

| The answer came from | Questions |
|---|---|
| documents | 53 |
| data | 29 |
| both | 26 |

## Latency

Milliseconds, p50 / p95 / max, by the paths that ran. The target is 3 s a query, without the backend. Route and paths are measured from the start, so they overlap.

| Paths | n | Under the target | Total | Route | Paths done | Answer from both |
|---|---|---|---|---|---|---|
| documents | 38 | 37 (97.4%) | 1746 / 2859 / 3323 | 1334 / 2023 / 2026 | 1746 / 2859 / 3323 | – |
| data | 29 | 10 (34.5%) | 3135 / 5307 / 5406 | 1546 / 2012 / 2414 | 1911 / 3346 / 4019 | – |
| both | 41 | 16 (39.0%) | 3968 / 7676 / 8723 | 1321 / 1830 / 1924 | 2153 / 4502 / 5400 | 1866 / 3517 / 3716 |
| all | 108 | 63 (58.3%) | 2630 / 5851 / 8723 | 1402 / 2012 / 2414 | 1894 / 3960 / 5400 | 1866 / 3517 / 3716 |

Over the target (45): `q-42d4ae2300` 8723 ms (documents+data, written by both, 1 queries); `q-b482d79dd3` 8019 ms (documents+data, written by both, 1 queries); `q-5a69071550` 7676 ms (documents+data, written by both, 1 queries); `q-40cb4f5cea` 6865 ms (documents+data, written by both, 1 queries); `q-e9a1a5276e` 5926 ms (documents+data, written by both, 1 queries); `q-54cc83b997` 5851 ms (documents+data, written by both, 1 queries); `q-e6ba74bc07` 5824 ms (documents+data, written by both, 1 queries); `q-1c63e12ded` 5406 ms (data, written by data, 1 queries); `q-7cb370d465` 5307 ms (data, written by data, 1 queries); `q-0f544827a5` 5289 ms (documents+data, written by both, 1 queries); `q-a5d100d152` 5274 ms (documents+data, written by both, 2 queries); `q-09f63cace2` 5240 ms (documents+data, written by both, 1 queries); `q-123c6ee65c` 5183 ms (data, written by data, 1 queries); `q-b57ed61f60` 5057 ms (documents+data, written by both, 1 queries); `q-9abc436ce5` 4907 ms (documents+data, written by both, 1 queries).

## Cost

$0.0478 in all, $0.000442 a question on average, the calls that finished only. By the part of the path:

| Part | Input tokens | Output tokens |
|---|---|---|
| router | 46616 | 4313 |
| rewriter | 70508 | 2979 |
| documents | 87660 | 3246 |
| writer | 131433 | 4266 |
| explainer | 9674 | 1036 |
| compose | 38692 | 2778 |

The documents' answer written while routing and then not used, because the answer came from both: 26 questions, 30240 input and 1701 output tokens.

## The rewrite

Rewritten: 11 of 108 (2 of the 6 follow-ups). A campaign found and named first: 8; asked back: 0; no campaign found: 0. Left as asked: stands on its own 75, the model wrote the question as it was 20, the model didn't rewrite it: TimeoutError 1, the model didn't rewrite it: APITimeoutError 1.

Total ms, p50 / p95 / max: started again on the rewritten question 5183 / 8723 / 8723 (11); left as asked 2245 / 5289 / 8019 (97). The rewrite itself, from the start: 1262 / 1916 / 2026.

| Question | Rewritten | Basis or candidates | Status |
|---|---|---|---|
| `q-42d4ae2300` what should we keep doing from the home black friday live? | what should we keep doing from the home {campaign} live? | Home Black Friday Live 2025-11-18 | answered |
| `q-cd428e41fe` How much GMV did last Black Friday's electronics livestream bring in? | How much GMV did {campaign}'s electronics livestream bring in? | Electronics Black Friday Live 2025-11-16 | answered |
| `q-123c6ee65c` What was the conversion rate for our most recent product launch? | What was the conversion rate for our {campaign}? | Electronics New Product Launch 2026-09-29 | answered |
| `q-617dabba49` How much GMV did the latest fan appreciation campaign generate? | How much GMV did the {campaign} generate? | Electronics Fan Appreciation Day 2026-08-20 | answered |
| `q-b66ecb8a0e` What was the average conversion rate for last Black Friday's grocery live? | What was the average conversion rate for {campaign}'s grocery live? | Grocery Black Friday Live 2025-11-12 | answered |
| `q-fa3c627a7f` Which of our grocery campaigns this year had the highest peak live audience, and who hosted it? | Which of our grocery campaigns {period} had the highest peak live audience, and who hosted it? | – | answered |
| `q-1c63e12ded` What was the highest peak concurrent viewer count among campaigns that started in the last three full months? | What was the highest peak concurrent viewer count among campaigns that started in {period}? | – | answered |
| `q-e9a1a5276e` For the next sale like last Beauty Black Friday, should we change the serum and sheet-mask offers, and what results in the review make the case? | For the next sale like {campaign}, should we change the serum and sheet-mask offers, and what results in the review make the case? | Beauty Black Friday Live 2025-11-18 | answered |
| `q-5a69071550` When we plan another anniversary sale, how should we rethink which products get airtime given the sales and returns from the last one? | When we plan another anniversary sale, how should we rethink which products get airtime given the sales and returns from {campaign}? | Home Anniversary Sale 2026-09-04 | answered |
| `q-18906f3f3f` Who signs off on it? | Who signs off on the Beauty sale event inventory check? | – | answered |
| `q-7cb370d465` And the one before that? | And the {campaign} before that? | Home New Product Launch 2026-03-01 | answered |

## The data answers

Questions that need the data and have a gold result: 39. The data path ran and came back with rows for 39; their rows hold the gold result for 32 (82.1%). Measured as in evaluation/sql_run.py: same number of rows, each holding its gold row's numbers.

## Questions to look at

| Question | Needed | Paths | Status | Note |
|---|---|---|---|---|
| `q-ebacddf621` For the Feb 14 Beauty Fan Appreciation Day, which product had the strongest margin? | documents | data | answered | a path missing |
| `q-71123ae9c2` How did Storage Bins perform on sales and returns in the Jan 8 versus Jan 18 Home New Product Launch? | documents | data | answered | a path missing |
