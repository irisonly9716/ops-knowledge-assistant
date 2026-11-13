# Query path evaluation run: dev-flow-1

108 questions from `eval/dev.jsonl`, through app/query_flow.py as /query runs it, one at a time. Statuses: answered 86, refused 22. 264 s in all.

## Where the questions went

A path missing: 3; a path extra: 35; decided by the keyword rule: 5. A path cancelled by the route: 28 questions (documents 18, data 10).

| The answer came from | Questions |
|---|---|
| documents | 52 |
| data | 28 |
| both | 28 |

## Latency

Milliseconds, p50 / p95 / max, by the paths that ran. The target is 3 s a query, without the backend. Route and paths are measured from the start, so they overlap.

| Paths | n | Under the target | Total | Route | Paths done | Answer from both |
|---|---|---|---|---|---|---|
| documents | 38 | 38 (100.0%) | 1564 / 1989 / 2017 | 1260 / 1801 / 2017 | 1564 / 1989 / 2017 | – |
| data | 28 | 22 (78.6%) | 2731 / 3208 / 3460 | 1176 / 2017 / 2029 | 1490 / 2046 / 2386 | – |
| both | 42 | 14 (33.3%) | 3221 / 5216 / 5425 | 1271 / 1844 / 2004 | 1817 / 2505 / 3107 | 1620 / 2629 / 2961 |
| all | 108 | 74 (68.5%) | 2017 / 4544 / 5425 | 1259 / 1885 / 2029 | 1631 / 2342 / 3107 | 1620 / 2629 / 2961 |

Over the target (34): `q-b482d79dd3` 5425 ms (documents+data, written by both, 1 queries); `q-40cb4f5cea` 5232 ms (documents+data, written by both, 1 queries); `q-f6c62704bd` 5216 ms (documents+data, written by both, 1 queries); `q-5a69071550` 4770 ms (documents+data, written by both, 1 queries); `q-e9a1a5276e` 4555 ms (documents+data, written by both, 1 queries); `q-e6ba74bc07` 4544 ms (documents+data, written by both, 1 queries); `q-0f544827a5` 4465 ms (documents+data, written by both, 1 queries); `q-b4107b87a7` 4400 ms (documents+data, written by both, 1 queries); `q-a5902b702d` 4239 ms (documents+data, written by both, 1 queries); `q-9abc436ce5` 3987 ms (documents+data, written by both, 1 queries); `q-09f63cace2` 3959 ms (documents+data, written by both, 1 queries); `q-524c9ea0a1` 3874 ms (documents+data, written by both, 1 queries); `q-7eea663201` 3765 ms (documents+data, written by both, 1 queries); `q-b57ed61f60` 3700 ms (documents+data, written by both, 1 queries); `q-cd28b24091` 3651 ms (documents+data, written by both, 1 queries).

## Cost

$0.0390 in all, $0.000361 a question on average, the calls that finished only. By the part of the path:

| Part | Input tokens | Output tokens |
|---|---|---|
| router | 45966 | 4271 |
| documents | 88269 | 3254 |
| writer | 127133 | 4127 |
| explainer | 8123 | 829 |
| compose | 43077 | 2992 |

The documents' answer written while routing and then not used, because the answer came from both: 28 questions, 34005 input and 1745 output tokens.

## The data answers

Questions that need the data and have a gold result: 39. The data path ran and came back with rows for 37; their rows hold the gold result for 30 (81.1%). Measured as in evaluation/sql_run.py: same number of rows, each holding its gold row's numbers.

## Questions to look at

| Question | Needed | Paths | Status | Note |
|---|---|---|---|---|
| `q-c6e9e26d00` And the Beauty one? | data | documents | refused | a path missing |
| `q-ebacddf621` For the Feb 14 Beauty Fan Appreciation Day, which product had the strongest margin? | documents | data | answered | a path missing |
| `q-71123ae9c2` How did Storage Bins perform on sales and returns in the Jan 8 versus Jan 18 Home New Product Launch? | documents | data | answered | a path missing |
