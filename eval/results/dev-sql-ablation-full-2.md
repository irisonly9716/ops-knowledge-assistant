# Text-to-SQL evaluation run: dev-sql-ablation-full-2

39 questions with a gold SQL from `eval/dev.jsonl`. Fixed pipeline (zero-shot + data dictionary + views + 3 examples + 2 retries), writing with gpt-6-luna and explaining with gpt-6-luna, 12 s a question, queries run as sql_reader. Statuses: answered 38, refused 1. 105 s in all.

## Execution accuracy

The last query's rows against the gold result: same number of rows, and each holds its gold row's numbers (within 0.5%), or all its values when it has no numbers. Strict: all of the gold row's values, names and dates included.

| Questions | n | Execution accuracy | Strict | Gold numbers found |
|---|---|---|---|---|
| **data: the number is the answer** | 27 | 85.2% | 63.0% | 90.4% |
| both: numbers that support an answer | 12 | 58.3% | 58.3% | 84.8% |
| data, direct | 8 | 87.5% | 75.0% | 93.8% |
| data, paraphrase | 3 | 100.0% | 100.0% | 100.0% |
| data, colloquial | 4 | 75.0% | 75.0% | 75.0% |
| data, vague | 9 | 77.8% | 22.2% | 87.5% |
| data, keyword | 3 | 100.0% | 100.0% | 100.0% |
| data, single_table | 5 | 80.0% | 80.0% | 80.0% |
| data, multi_table | 10 | 100.0% | 100.0% | 100.0% |
| data, time_range | 5 | 80.0% | 60.0% | 90.0% |
| data, multi_turn | 2 | 0.0% | 0.0% | 0.0% |
| data, asked by staff | 15 | 93.3% | 60.0% | 93.3% |
| data, asked by manager | 5 | 100.0% | 100.0% | 100.0% |
| data, asked by executive | 7 | 57.1% | 42.9% | 75.0% |

## The asker's department

Answered queries for staff and managers that ran with the department filter the code adds: 32 of 32.

## Writing and correcting

Answered on the first query: 37; after correcting: 1 after 1 retry. Of the corrected ones, 0 of 1 were right.

Queries that failed: 1 (UndefinedColumn 1).
- `q-0f544827a5`: UndefinedColumn: column "start_date" does not exist LINE 1: SELECT campaign_name, start_date, product_name, units_sold, ...                               ^

## Wrong

| Question | Status | Gold, first row | Last query, first row |
|---|---|---|---|
| `q-b66ecb8a0e` What was the average conversion rate for last Black Friday's grocery live? | answered | 0.08838571428571428571 | – (0 rows) |
| `q-28d5d41079` What was the average conversion rate for the Fresh Grocery campaigns that started in Q3 2026? | answered | 0.08085170194003527337, 0.08225882352941176471 | 0.08085170194003527337 (1 row) |
| `q-40cb4f5cea` How did the product mix and returns affect the Black Friday grocery livestream’s results against its targets? | answered | Grocery Black Friday Live, 2025-11-12, 181056.65, 0.08838571428571428571, Fresh Cherries, 38189.56, 20097.08, 0.0147 | Grocery Black Friday Live, 2025-11-12, 28866.58, 34027.23, 146741.31, 68570.77, 2.0151734360980897, Fresh Cherries, 224, 38189.56, 20097.08, 0.0147, 0.1881, 0.5 … (6 rows) |
| `q-9abc436ce5` Why did the Feb 14 Beauty Fan Appreciation Day meet its sales and audience targets, and which product did most of the work? | answered | 766115.13, 0.08743243243243243243, 6496, Eyeshadow Palette, 4808, 507384.63, 199336.07 | Beauty Fan Appreciation Day, 2026-02-14, 2026-03-22, finished, 144354.08, 155117.55, 37, 766115.13, 0.08743243243243243243, 6496, 1800.4864864864864865, 54.7856 … (1 row) |
| `q-09f63cace2` How did the Feb 14 Beauty Fan Appreciation Day end up on ROI and operating cost versus plan, and what should we check before the next one? | answered | 144354.08, 155117.55, 247515.36, 1.5956631599712605, 0.07456297736787211002 | Beauty Fan Appreciation Day, 2026-02-14, 144354.08, 155117.55, 1.5956631599712605 (1 row) |
| `q-b57ed61f60` Why did the Jan 18 Home New Product Launch meet its sales and conversion targets but miss the viewer and cost targets? | answered | Home New Product Launch, 2026-01-18, 587611.94, 0.08822631578947368421, 8250, 113043.09, 138343.78 | Home New Product Launch, 2026-01-18, 2026-02-05, finished, 472533.34, 0.08822631578947368421, 2588.0000000000000000, 113043.09, 138343.78, 207841.73, 1.50235688 … (1 row) |
| `q-0f544827a5` For the Jan 18 launch, how did the product mix help sales land on target, and what product issues still need attention? | answered | Home New Product Launch, 2026-01-18, 587611.94, Paper Towels, 175001.62, 85811.02, 0.1030 | Home New Product Launch, 2026-01-18, Paper Towels, 1290, 175001.62, 85811.02, 0.1030, 0.9089, 0.0936, 0.4903 (5 rows) |
| `q-7cb370d465` And the one before that? | refused | Home New Product Launch, 2026-03-01, 2026-03-17 | – (no rows) |
| `q-c6e9e26d00` And the Beauty one? | answered | Beauty Black Friday Live, 2025-11-18, 2025-12-14 | Beauty Anniversary Sale, 2026-09-07, 210578.76, 253967.33, 1285947.20, 0.08355937500000000000, 13294, 3781.0937500000000000, 59.5303125000000000, 14.66093750000 … (1 row) |

## Latency and cost

| | p50 | p95 | max |
|---|---|---|---|
| ms a question | 2199 | 4814 | 5206 |
| embedding, ms | 181 | 283 | 315 |
| schema, names and examples, ms | 48 | 55 | 56 |
| writing queries, ms | 1173 | 3045 | 3331 |
| running queries, ms | 6 | 21 | 32 |
| explaining, ms | 987 | 2048 | 2463 |
| model calls | 2 | 2 | 3 |
| queries run | 1 | 1 | 2 |

$0.0112 in all, $0.00029 a question; input 2228 and output 130 tokens a question on average.

## Instructions

As for a staff member in Beauty:

```
You write one PostgreSQL query that answers a question about a live-commerce company's livestream campaigns.

The question comes from a staff in the Beauty department, who may only see that department's data. Everything listed below already holds only Beauty's rows, so don't filter by department for that. Today is 2026-10-15.

Rules:
- Use only the views or tables listed below and their columns.
- Reply with the query alone: one SELECT statement (WITH is fine), no explanation, no code fence.
- Next to numbers about a campaign, return its name and start date: several campaigns can share a name.
- Add a LIMIT only when the question asks for a number of rows, or for the top or bottom one.
- Keep rates, shares and ROI as the stored fractions: don't multiply them by 100 or round them.
- When ordering by a column that can be NULL, put NULLs last.
- Write dates as literals worked out from today's date, not with CURRENT_DATE.
- A question that asks why, what to change or what to do next still gets a query: return the numbers about what it names, such as a campaign's results against its plan or its products' sales and returns. The reasons and advice come from documents elsewhere.
- Reply with exactly NOT_IN_DATA only when nothing listed below holds what the question is about, such as the weather or salaries. A question about a campaign, product, host, region or number listed here always gets a query.
```
