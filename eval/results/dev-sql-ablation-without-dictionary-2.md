# Text-to-SQL evaluation run: dev-sql-ablation-without-dictionary-2

39 questions with a gold SQL from `eval/dev.jsonl`. Fixed pipeline (zero-shot + views + 3 examples + 2 retries), writing with gpt-6-luna and explaining with gpt-6-luna, 12 s a question, queries run as sql_reader. Statuses: answered 38, refused 1. 92 s in all.

## Execution accuracy

The last query's rows against the gold result: same number of rows, and each holds its gold row's numbers (within 0.5%), or all its values when it has no numbers. Strict: all of the gold row's values, names and dates included.

| Questions | n | Execution accuracy | Strict | Gold numbers found |
|---|---|---|---|---|
| **data: the number is the answer** | 27 | 70.4% | 55.6% | 75.0% |
| both: numbers that support an answer | 12 | 25.0% | 25.0% | 62.9% |
| data, direct | 8 | 87.5% | 75.0% | 93.8% |
| data, paraphrase | 3 | 66.7% | 66.7% | 66.7% |
| data, colloquial | 4 | 50.0% | 50.0% | 50.0% |
| data, vague | 9 | 55.6% | 22.2% | 62.5% |
| data, keyword | 3 | 100.0% | 100.0% | 100.0% |
| data, single_table | 5 | 60.0% | 60.0% | 60.0% |
| data, multi_table | 10 | 90.0% | 90.0% | 90.0% |
| data, time_range | 5 | 80.0% | 60.0% | 90.0% |
| data, multi_turn | 2 | 0.0% | 0.0% | 0.0% |
| data, asked by staff | 15 | 66.7% | 46.7% | 66.7% |
| data, asked by manager | 5 | 100.0% | 100.0% | 100.0% |
| data, asked by executive | 7 | 57.1% | 42.9% | 75.0% |

## The asker's department

Answered queries for staff and managers that ran with the department filter the code adds: 32 of 32.

## Writing and correcting

Answered on the first query: 38; after correcting: none. Of the corrected ones, 0 of 0 were right.

Queries that failed: 0.

## Wrong

| Question | Status | Gold, first row | Last query, first row |
|---|---|---|---|
| `q-cd428e41fe` How much GMV did last Black Friday's electronics livestream bring in? | answered | Electronics Black Friday Live, 2025-11-16, 2025-12-12, 1043330.18 | – (0 rows) |
| `q-2d8f4b1df2` What was the peak live audience for our summer electronics sale? | answered | Electronics Summer Sale, 2026-06-10, 2026-07-14, 15568 | – (0 rows) |
| `q-d782ff8bc5` How many viewers were watching at once at the busiest point of the summer sale? | answered | 4937 | – (0 rows) |
| `q-b66ecb8a0e` What was the average conversion rate for last Black Friday's grocery live? | answered | 0.08838571428571428571 | – (0 rows) |
| `q-faeada22c8` which region spent the most on our back-to-school grocery live, and how much? | answered | Southwest, 9539.90 | – (0 rows) |
| `q-28d5d41079` What was the average conversion rate for the Fresh Grocery campaigns that started in Q3 2026? | answered | 0.08085170194003527337, 0.08225882352941176471 | 0.08085170194003527337 (1 row) |
| `q-e9a1a5276e` For the next sale like last Beauty Black Friday, should we change the serum and sheet-mask offers, and what results in the review make the case? | answered | Beauty Black Friday Live, 2025-11-18, Sheet Mask, 470, 131623.43, 45439.53, 0.3452 | – (0 rows) |
| `q-5a69071550` When we plan another anniversary sale, how should we rethink which products get airtime given the sales and returns from the last one? | answered | Home Anniversary Sale, 2026-09-04, Spin Mop, 724, 165723.20, 86394.52, 0.5213, 0.1990 | Spin Mop, 724, 165723.20, 86394.52, 0.1990, 0.8264 (6 rows) |
| `q-b482d79dd3` When we build the next summer electronics lineup, which products should get a bigger role or a closer look, and what results in the 2026 Summer Sale review support that? | answered | Electronics Summer Sale, 2026-06-10, Wireless Earbuds, 244397.60, 135535.37, 0.5546, 0.1079 | – (0 rows) |
| `q-cd28b24091` Why did Grocery Black Friday Live hit its GMV and audience targets but miss conversion and go over budget? | answered | Grocery Black Friday Live, 2025-11-12, 28866.58, 34027.23, 181056.65, 0.08838571428571428571, 1960 | Grocery Black Friday Live, 2025-11-12, 181056.65, 573.8928571428571429, 0.08838571428571428571, 28866.58, 34027.23 (1 row) |
| `q-40cb4f5cea` How did the product mix and returns affect the Black Friday grocery livestream’s results against its targets? | answered | Grocery Black Friday Live, 2025-11-12, 181056.65, 0.08838571428571428571, Fresh Cherries, 38189.56, 20097.08, 0.0147 | Grocery Black Friday Live, 2025-11-12, 28866.58, 34027.23, 181056.65, 146741.31, 68570.77, 2.0151734360980897, Fresh Cherries, 224, 38189.56, 20097.08, 0.0147,  … (6 rows) |
| `q-9abc436ce5` Why did the Feb 14 Beauty Fan Appreciation Day meet its sales and audience targets, and which product did most of the work? | answered | 766115.13, 0.08743243243243243243, 6496, Eyeshadow Palette, 4808, 507384.63, 199336.07 | – (0 rows) |
| `q-09f63cace2` How did the Feb 14 Beauty Fan Appreciation Day end up on ROI and operating cost versus plan, and what should we check before the next one? | answered | 144354.08, 155117.55, 247515.36, 1.5956631599712605, 0.07456297736787211002 | Beauty Fan Appreciation Day, 2026-02-14, 1.5956631599712605, 144354.08, 155117.55 (1 row) |
| `q-b57ed61f60` Why did the Jan 18 Home New Product Launch meet its sales and conversion targets but miss the viewer and cost targets? | answered | Home New Product Launch, 2026-01-18, 587611.94, 0.08822631578947368421, 8250, 113043.09, 138343.78 | Home New Product Launch, 2026-01-18, 472533.34, 0.08822631578947368421, 8250, 113043.09, 138343.78 (1 row) |
| `q-0f544827a5` For the Jan 18 launch, how did the product mix help sales land on target, and what product issues still need attention? | answered | Home New Product Launch, 2026-01-18, 587611.94, Paper Towels, 175001.62, 85811.02, 0.1030 | Home New Product Launch, 2026-01-18, Paper Towels, 1290, 175001.62, 85811.02, 0.9089, 0.1030, 0.0936, 0.4903 (5 rows) |
| `q-7cb370d465` And the one before that? | refused | Home New Product Launch, 2026-03-01, 2026-03-17 | – (no rows) |
| `q-c6e9e26d00` And the Beauty one? | answered | Beauty Black Friday Live, 2025-11-18, 2025-12-14 | Beauty Weekly Live Special, 2026-09-28, 518082.35, NULL, NULL, NULL, NULL (1 row) |

## Latency and cost

| | p50 | p95 | max |
|---|---|---|---|
| ms a question | 2258 | 3832 | 4028 |
| embedding, ms | 201 | 393 | 452 |
| schema, names and examples, ms | 46 | 50 | 51 |
| writing queries, ms | 1120 | 1863 | 2435 |
| running queries, ms | 5 | 12 | 33 |
| explaining, ms | 875 | 2023 | 2023 |
| model calls | 2 | 2 | 2 |
| queries run | 1 | 1 | 1 |

$0.0071 in all, $0.00018 a question; input 1294 and output 106 tokens a question on average.

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
