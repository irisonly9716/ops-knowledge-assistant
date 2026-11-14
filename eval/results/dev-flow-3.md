# Query path evaluation run: dev-flow-3

108 questions from `eval/dev.jsonl`, through app/query_flow.py as /query runs it, one at a time. Statuses: answered 89, refused 19. 282 s in all.

## Where the questions went

A path missing: 2; a path extra: 34; decided by the keyword rule: 2. A path cancelled by the route: 32 questions (documents 17, data 15).

| The answer came from | Questions |
|---|---|
| documents | 51 |
| data | 29 |
| both | 28 |

## Latency

Milliseconds, p50 / p95 / max, by the paths that ran. The target is 3 s a query, without the backend. Route and paths are measured from the start, so they overlap.

| Paths | n | Under the target | Total | Route | Paths done | Answer from both |
|---|---|---|---|---|---|---|
| documents | 37 | 36 (97.3%) | 1597 / 2740 / 3048 | 1227 / 2003 / 2009 | 1597 / 2740 / 3046 | – |
| data | 29 | 17 (58.6%) | 2760 / 4548 / 5196 | 1181 / 1466 / 1661 | 1550 / 3339 / 3839 | – |
| both | 42 | 17 (40.5%) | 3331 / 5199 / 6580 | 1236 / 1695 / 1918 | 1850 / 3101 / 3829 | 1724 / 2675 / 3480 |
| all | 108 | 70 (64.8%) | 2547 / 4548 / 6580 | 1226 / 1779 / 2009 | 1726 / 3046 / 3839 | 1724 / 2675 / 3480 |

Over the target (38): `q-5a69071550` 6580 ms (documents+data, written by both, 1 queries); `q-e9a1a5276e` 5475 ms (documents+data, written by both, 1 queries); `q-49e0dba4c8` 5199 ms (documents+data, written by both, 2 queries); `q-ba660b5f7c` 5196 ms (data, written by data, 1 queries); `q-40cb4f5cea` 4717 ms (documents+data, written by both, 1 queries); `q-617dabba49` 4548 ms (data, written by data, 1 queries); `q-b482d79dd3` 4445 ms (documents+data, written by both, 1 queries); `q-e6ba74bc07` 4434 ms (documents+data, written by both, 1 queries); `q-a5902b702d` 4418 ms (documents+data, written by both, 1 queries); `q-b4107b87a7` 4413 ms (documents+data, written by both, 1 queries); `q-0f544827a5` 4360 ms (documents+data, written by both, 1 queries); `q-fa3c627a7f` 4108 ms (data, written by data, 1 queries); `q-09f63cace2` 4040 ms (documents+data, written by both, 1 queries); `q-b57ed61f60` 3966 ms (documents+data, written by both, 1 queries); `q-cd428e41fe` 3931 ms (data, written by data, 1 queries).

## Cost

$0.0537 in all, $0.000497 a question on average, the calls that finished only. By the part of the path:

| Part | Input tokens | Output tokens |
|---|---|---|
| router | 47518 | 4366 |
| rewriter | 129615 | 2704 |
| documents | 86613 | 3022 |
| writer | 131295 | 4374 |
| explainer | 9088 | 902 |
| compose | 42055 | 2840 |

The documents' answer written while routing and then not used, because the answer came from both: 28 questions, 32721 input and 1546 output tokens.

## The rewrite

Rewritten: 14 of 108 (5 of the 6 follow-ups). A campaign found and named first: 8; asked back: 0; no campaign found: 0. Left as asked: stands on its own 92, the model wrote the question as it was 2.

Total ms, p50 / p95 / max: started again on the rewritten question 3727 / 6580 / 6580 (14); left as asked 2115 / 4434 / 5199 (94). The rewrite itself, from the start: 888 / 1442 / 1914.

| Question | Rewritten | Basis or candidates | Status |
|---|---|---|---|
| `q-42d4ae2300` what should we keep doing from the home black friday live? | What should we keep doing from {campaign}? | Home Black Friday Live 2025-11-18 | answered |
| `q-cd428e41fe` How much GMV did last Black Friday's electronics livestream bring in? | How much GMV did {campaign}'s electronics livestream bring in? | Electronics Black Friday Live 2025-11-16 | answered |
| `q-123c6ee65c` What was the conversion rate for our most recent product launch? | What was the conversion rate for {campaign}? | Electronics New Product Launch 2026-09-29 | answered |
| `q-617dabba49` How much GMV did the latest fan appreciation campaign generate? | How much GMV did {campaign} generate? | Electronics Fan Appreciation Day 2026-08-20 | answered |
| `q-b66ecb8a0e` What was the average conversion rate for last Black Friday's grocery live? | What was the average conversion rate for {campaign}'s grocery live? | Grocery Black Friday Live 2025-11-12 | answered |
| `q-fa3c627a7f` Which of our grocery campaigns this year had the highest peak live audience, and who hosted it? | Which of our grocery campaigns in {period} had the highest peak live audience, and who hosted it? | – | answered |
| `q-1c63e12ded` What was the highest peak concurrent viewer count among campaigns that started in the last three full months? | What was the highest peak concurrent viewer count among campaigns that started in {period}? | – | answered |
| `q-e9a1a5276e` For the next sale like last Beauty Black Friday, should we change the serum and sheet-mask offers, and what results in the review make the case? | For the next sale like {campaign}, should we change the serum and sheet-mask offers, and what results in the review make the case? | Beauty Black Friday Live 2025-11-18 | answered |
| `q-5a69071550` When we plan another anniversary sale, how should we rethink which products get airtime given the sales and returns from the last one? | When we plan another anniversary sale, how should we rethink which products get airtime given the sales and returns from {campaign}? | Home Anniversary Sale 2026-09-04 | answered |
| `q-18906f3f3f` Who signs off on it? | Who signs off on the Beauty sale event inventory check? | – | answered |
| `q-40d099213e` And what do we check in the product demonstrations? | What do we check in the product demonstrations after an Electronics livestream? | – | answered |
| `q-8899a36ebc` and where do we log the findings? | Where do we log the findings from correcting a wrong product spec shown on screen? | – | answered |
| `q-7cb370d465` And the one before that? | When was the {campaign} before the most recent finished one? | Home New Product Launch 2026-03-01 | answered |
| `q-c6e9e26d00` And the Beauty one? | When did the Beauty Black Friday Live campaign run? | – | answered |

## The data answers

Questions that need the data and have a gold result: 39. The data path ran and came back with rows for 39; their rows hold the gold result for 33 (84.6%). Measured as in evaluation/sql_run.py: same number of rows, each holding its gold row's numbers.

## Questions to look at

| Question | Needed | Paths | Status | Note |
|---|---|---|---|---|
| `q-ebacddf621` For the Feb 14 Beauty Fan Appreciation Day, which product had the strongest margin? | documents | data | answered | a path missing |
| `q-71123ae9c2` How did Storage Bins perform on sales and returns in the Jan 8 versus Jan 18 Home New Product Launch? | documents | data | answered | a path missing |
