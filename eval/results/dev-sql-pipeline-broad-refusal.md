# Text-to-SQL evaluation run: dev-sql-pipeline-broad-refusal

39 questions with a gold SQL from `eval/dev.jsonl`. Fixed pipeline (zero-shot + data dictionary + views + 3 examples + 2 retries), writing with gpt-6-luna and explaining with gpt-6-luna, 12 s a question. Statuses: answered 24, refused 14, timeout 1. 92 s in all.

## Execution accuracy

The last query's rows against the gold result: same number of rows, and each holds its gold row's numbers (within 0.5%), or all its values when it has no numbers. Strict: all of the gold row's values, names and dates included.

| Questions | n | Execution accuracy | Strict | Gold numbers found |
|---|---|---|---|---|
| **data: the number is the answer** | 27 | 74.1% | 59.3% | 78.8% |
| both: numbers that support an answer | 12 | 0.0% | 0.0% | 6.7% |
| data, direct | 8 | 87.5% | 75.0% | 93.8% |
| data, paraphrase | 3 | 66.7% | 66.7% | 66.7% |
| data, colloquial | 4 | 75.0% | 75.0% | 75.0% |
| data, vague | 9 | 55.6% | 22.2% | 62.5% |
| data, keyword | 3 | 100.0% | 100.0% | 100.0% |
| data, single_table | 5 | 80.0% | 80.0% | 80.0% |
| data, multi_table | 10 | 90.0% | 90.0% | 90.0% |
| data, time_range | 5 | 80.0% | 60.0% | 90.0% |
| data, multi_turn | 2 | 0.0% | 0.0% | 0.0% |
| data, asked by staff | 15 | 80.0% | 60.0% | 80.0% |
| data, asked by manager | 5 | 80.0% | 80.0% | 80.0% |
| data, asked by executive | 7 | 57.1% | 42.9% | 75.0% |

## The asker's department

Answered queries for staff and managers that ran with the department filter the code adds: 18 of 18.

## Writing and correcting

Answered on the first query: 23; after correcting: 1 after 1 retry. Of the corrected ones, 0 of 1 were right.

Queries that failed: 1 (UndefinedColumn 1).
- `q-40cb4f5cea`: UndefinedColumn: column "campaign_start_date" does not exist LINE 1: SELECT campaign_name, campaign_start_date, status, planned_o...                            

## Wrong

| Question | Status | Gold, first row | Last query, first row |
|---|---|---|---|
| `q-cd428e41fe` How much GMV did last Black Friday's electronics livestream bring in? | refused | Electronics Black Friday Live, 2025-11-16, 2025-12-12, 1043330.18 | – (no rows) |
| `q-2d8f4b1df2` What was the peak live audience for our summer electronics sale? | refused | Electronics Summer Sale, 2026-06-10, 2026-07-14, 15568 | – (no rows) |
| `q-d782ff8bc5` How many viewers were watching at once at the busiest point of the summer sale? | timeout | 4937 | – (no rows) |
| `q-809209a387` Which product brought in the most sales during our Beauty summer sale, and what was its return rate? | refused | Facial Cleanser, 446455.01, 0.1650 | – (no rows) |
| `q-28d5d41079` What was the average conversion rate for the Fresh Grocery campaigns that started in Q3 2026? | answered | 0.08085170194003527337, 0.08225882352941176471 | 0.08085170194003527337 (1 row) |
| `q-f6c62704bd` For our next Beauty Black Friday live, how should we set the operating-cost budget, and which results from the 2025 review support that change? | refused | Beauty Black Friday Live, 2025-11-18, 71061.53, 73552.32 | – (no rows) |
| `q-e9a1a5276e` For the next sale like last Beauty Black Friday, should we change the serum and sheet-mask offers, and what results in the review make the case? | refused | Beauty Black Friday Live, 2025-11-18, Sheet Mask, 470, 131623.43, 45439.53, 0.3452 | – (no rows) |
| `q-e6ba74bc07` For the next Home Anniversary Sale, what should we change to close the sales gap without losing what worked in the 2026 campaign? | refused | Home Anniversary Sale, 2026-09-04, 766083.21, 0.07964285714285714286, 10978, 89174.11, 80775.37 | – (no rows) |
| `q-5a69071550` When we plan another anniversary sale, how should we rethink which products get airtime given the sales and returns from the last one? | refused | Home Anniversary Sale, 2026-09-04, Spin Mop, 724, 165723.20, 86394.52, 0.5213, 0.1990 | – (no rows) |
| `q-a5902b702d` For the next Electronics Summer Sale, what should we change about traffic and spending plans, based on the 2026 post-campaign review and the campaign results? | refused | Electronics Summer Sale, 2026-06-10, 1717497.47, 0.08010000000000000000, 15568, 182684.30, 186748.69 | – (no rows) |
| `q-b482d79dd3` When we build the next summer electronics lineup, which products should get a bigger role or a closer look, and what results in the 2026 Summer Sale review support that? | refused | Electronics Summer Sale, 2026-06-10, Wireless Earbuds, 244397.60, 135535.37, 0.5546, 0.1079 | – (no rows) |
| `q-cd28b24091` Why did Grocery Black Friday Live hit its GMV and audience targets but miss conversion and go over budget? | refused | Grocery Black Friday Live, 2025-11-12, 28866.58, 34027.23, 181056.65, 0.08838571428571428571, 1960 | – (no rows) |
| `q-40cb4f5cea` How did the product mix and returns affect the Black Friday grocery livestream’s results against its targets? | answered | Grocery Black Friday Live, 2025-11-12, 181056.65, 0.08838571428571428571, Fresh Cherries, 38189.56, 20097.08, 0.0147 | Grocery Black Friday Live, 2025-11-12, finished, 28866.58, 34027.23, 181056.65, 146741.31, 68570.77, 2.0151734360980897 (1 row) |
| `q-9abc436ce5` Why did the Feb 14 Beauty Fan Appreciation Day meet its sales and audience targets, and which product did most of the work? | refused | 766115.13, 0.08743243243243243243, 6496, Eyeshadow Palette, 4808, 507384.63, 199336.07 | – (no rows) |
| `q-09f63cace2` How did the Feb 14 Beauty Fan Appreciation Day end up on ROI and operating cost versus plan, and what should we check before the next one? | answered | 144354.08, 155117.55, 247515.36, 1.5956631599712605, 0.07456297736787211002 | Beauty Fan Appreciation Day, 2026-02-14, 1.5956631599712605, 155117.55, 144354.08 (1 row) |
| `q-b57ed61f60` Why did the Jan 18 Home New Product Launch meet its sales and conversion targets but miss the viewer and cost targets? | refused | Home New Product Launch, 2026-01-18, 587611.94, 0.08822631578947368421, 8250, 113043.09, 138343.78 | – (no rows) |
| `q-0f544827a5` For the Jan 18 launch, how did the product mix help sales land on target, and what product issues still need attention? | refused | Home New Product Launch, 2026-01-18, 587611.94, Paper Towels, 175001.62, 85811.02, 0.1030 | – (no rows) |
| `q-7cb370d465` And the one before that? | refused | Home New Product Launch, 2026-03-01, 2026-03-17 | – (no rows) |
| `q-c6e9e26d00` And the Beauty one? | answered | Beauty Black Friday Live, 2025-11-18, 2025-12-14 | – (0 rows) |

## Latency and cost

| | p50 | p95 | max |
|---|---|---|---|
| ms a question | 2383 | 4414 | 12002 |
| embedding, ms | 184 | 477 | 626 |
| schema, names and examples, ms | 49 | 53 | 53 |
| writing queries, ms | 1068 | 1924 | 2049 |
| running queries, ms | 4 | 12 | 24 |
| explaining, ms | 885 | 1644 | 2103 |
| model calls | 2 | 2 | 3 |
| queries run | 1 | 1 | 2 |

$0.0086 in all, $0.00022 a question; input 1915 and output 58 tokens a question on average.

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
- If the question also asks for reasons or advice, return the numbers that bear on it; the reasons come from elsewhere.
- If the data can't answer the question at all, reply with exactly NOT_IN_DATA.
```
