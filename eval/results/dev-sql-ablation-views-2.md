# Text-to-SQL evaluation run: dev-sql-ablation-views-2

39 questions with a gold SQL from `eval/dev.jsonl`. Fixed pipeline (zero-shot + data dictionary + views), writing with gpt-6-luna and explaining with gpt-6-luna, 12 s a question, queries run as sql_reader. Statuses: answered 36, failed 2, refused 1. 104 s in all.

## Execution accuracy

The last query's rows against the gold result: same number of rows, and each holds its gold row's numbers (within 0.5%), or all its values when it has no numbers. Strict: all of the gold row's values, names and dates included.

| Questions | n | Execution accuracy | Strict | Gold numbers found |
|---|---|---|---|---|
| **data: the number is the answer** | 27 | 85.2% | 59.3% | 88.5% |
| both: numbers that support an answer | 12 | 66.7% | 66.7% | 79.6% |
| data, direct | 8 | 75.0% | 62.5% | 75.0% |
| data, paraphrase | 3 | 100.0% | 66.7% | 100.0% |
| data, colloquial | 4 | 75.0% | 75.0% | 75.0% |
| data, vague | 9 | 88.9% | 33.3% | 100.0% |
| data, keyword | 3 | 100.0% | 100.0% | 100.0% |
| data, single_table | 5 | 100.0% | 100.0% | 100.0% |
| data, multi_table | 10 | 100.0% | 90.0% | 100.0% |
| data, time_range | 5 | 60.0% | 40.0% | 60.0% |
| data, multi_turn | 2 | 0.0% | 0.0% | 0.0% |
| data, asked by staff | 15 | 100.0% | 66.7% | 100.0% |
| data, asked by manager | 5 | 100.0% | 80.0% | 100.0% |
| data, asked by executive | 7 | 42.9% | 28.6% | 50.0% |

## The asker's department

Answered queries for staff and managers that ran with the department filter the code adds: 30 of 30.

## Writing and correcting

Answered on the first query: 36; after correcting: none. Of the corrected ones, 0 of 0 were right.

Queries that failed: 2 (AmbiguousColumn 2).
- `q-40cb4f5cea`: AmbiguousColumn: column reference "campaign_name" is ambiguous LINE 1: SELECT campaign_name, start_date, status, planned_operating_...                ^
- `q-9abc436ce5`: AmbiguousColumn: column reference "campaign_name" is ambiguous LINE 1: SELECT campaign_name, start_date, planned_operating_cost, ac...                ^

## Wrong

| Question | Status | Gold, first row | Last query, first row |
|---|---|---|---|
| `q-2abe10703f` What was the total GMV for campaigns that started in September 2026? | answered | 3973234.47 | Electronics Anniversary Sale, 2026-09-02, 227761.04 (6 rows) |
| `q-28d5d41079` What was the average conversion rate for the Fresh Grocery campaigns that started in Q3 2026? | answered | 0.08085170194003527337, 0.08225882352941176471 | Fresh Grocery, Grocery Fan Appreciation Day, 2026-07-17, 0.07423500000000000000 (3 rows) |
| `q-40cb4f5cea` How did the product mix and returns affect the Black Friday grocery livestream’s results against its targets? | failed | Grocery Black Friday Live, 2025-11-12, 181056.65, 0.08838571428571428571, Fresh Cherries, 38189.56, 20097.08, 0.0147 | – (no rows) |
| `q-9abc436ce5` Why did the Feb 14 Beauty Fan Appreciation Day meet its sales and audience targets, and which product did most of the work? | failed | 766115.13, 0.08743243243243243243, 6496, Eyeshadow Palette, 4808, 507384.63, 199336.07 | – (no rows) |
| `q-09f63cace2` How did the Feb 14 Beauty Fan Appreciation Day end up on ROI and operating cost versus plan, and what should we check before the next one? | answered | 144354.08, 155117.55, 247515.36, 1.5956631599712605, 0.07456297736787211002 | Beauty Fan Appreciation Day, 2026-02-14, finished, 144354.08, 155117.55, 10763.47, 247515.36, 1.5956631599712605 (1 row) |
| `q-0f544827a5` For the Jan 18 launch, how did the product mix help sales land on target, and what product issues still need attention? | answered | Home New Product Launch, 2026-01-18, 587611.94, Paper Towels, 175001.62, 85811.02, 0.1030 | Home New Product Launch, 2026-01-18, 113043.09, 138343.78, 472533.34, 3096, Paper Towels, 1290, 175001.62, 0.9089, 0.1030, 0.0936, 85811.02, 0.4903 (5 rows) |
| `q-7cb370d465` And the one before that? | refused | Home New Product Launch, 2026-03-01, 2026-03-17 | – (no rows) |
| `q-c6e9e26d00` And the Beauty one? | answered | Beauty Black Friday Live, 2025-11-18, 2025-12-14 | Beauty Black Friday Live, 2025-11-18, Beauty, finished, 71061.53, 73552.32, 600441.45, 0.08617037037037037037, 6488, 2061.7777777777777778, 44.8155555555555556, … (11 rows) |

## Latency and cost

| | p50 | p95 | max |
|---|---|---|---|
| ms a question | 2380 | 5371 | 5890 |
| embedding, ms | 0 | 0 | 0 |
| schema, names and examples, ms | 6 | 12 | 67 |
| writing queries, ms | 1228 | 3923 | 3984 |
| running queries, ms | 8 | 14 | 36 |
| explaining, ms | 1171 | 2084 | 2633 |
| model calls | 2 | 2 | 2 |
| queries run | 1 | 1 | 1 |

$0.0103 in all, $0.00026 a question; input 1926 and output 141 tokens a question on average.

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
