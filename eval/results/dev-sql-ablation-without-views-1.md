# Text-to-SQL evaluation run: dev-sql-ablation-without-views-1

39 questions with a gold SQL from `eval/dev.jsonl`. Fixed pipeline (zero-shot + data dictionary + 3 examples + 2 retries), writing with gpt-6-luna and explaining with gpt-6-luna, 12 s a question, queries run as the tables' owner. Statuses: answered 38, refused 1. 144 s in all.

## Execution accuracy

The last query's rows against the gold result: same number of rows, and each holds its gold row's numbers (within 0.5%), or all its values when it has no numbers. Strict: all of the gold row's values, names and dates included.

| Questions | n | Execution accuracy | Strict | Gold numbers found |
|---|---|---|---|---|
| **data: the number is the answer** | 27 | 70.4% | 51.9% | 76.9% |
| both: numbers that support an answer | 12 | 41.7% | 41.7% | 81.4% |
| data, direct | 8 | 62.5% | 62.5% | 68.8% |
| data, paraphrase | 3 | 66.7% | 66.7% | 66.7% |
| data, colloquial | 4 | 75.0% | 75.0% | 75.0% |
| data, vague | 9 | 77.8% | 22.2% | 87.5% |
| data, keyword | 3 | 66.7% | 66.7% | 83.3% |
| data, single_table | 5 | 80.0% | 80.0% | 80.0% |
| data, multi_table | 10 | 80.0% | 80.0% | 85.0% |
| data, time_range | 5 | 40.0% | 40.0% | 50.0% |
| data, multi_turn | 2 | 0.0% | 0.0% | 0.0% |
| data, asked by staff | 15 | 86.7% | 53.3% | 86.7% |
| data, asked by manager | 5 | 80.0% | 80.0% | 90.0% |
| data, asked by executive | 7 | 28.6% | 28.6% | 41.7% |

## The asker's department

Answered queries for staff and managers that ran with the department filter the code adds: 32 of 32.

## Writing and correcting

Answered on the first query: 34; after correcting: 4 after 1 retry. Of the corrected ones, 1 of 4 were right.

Queries that failed: 4 (SqlRejected 4).
- `q-2c510597be`: SqlRejected: no such view: campaign_summary. The views are: departments, hosts, campaigns, campaign_metrics, campaign_products, campaign_customer_behavior
- `q-2abe10703f`: SqlRejected: no such view: campaign_summary. The views are: departments, hosts, campaigns, campaign_metrics, campaign_products, campaign_customer_behavior
- `q-5a69071550`: SqlRejected: no such view: campaign_product_sales. The views are: departments, hosts, campaigns, campaign_metrics, campaign_products, campaign_customer_behavior
- `q-40cb4f5cea`: SqlRejected: the query doesn't parse: Invalid expression / Unexpected token at line 17, column 5

## Wrong

| Question | Status | Gold, first row | Last query, first row |
|---|---|---|---|
| `q-b66ecb8a0e` What was the average conversion rate for last Black Friday's grocery live? | answered | 0.08838571428571428571 | – (0 rows) |
| `q-db8a36c5f0` What was the ROI for the September 2026 grocery anniversary campaign? | answered | Grocery Anniversary Sale, 2026-09-10, 1.9078150762760911 | Grocery Anniversary Sale, 2026-09-10, 0.90781507627609108596 (1 row) |
| `q-2c510597be` For the Beauty New Product Launch, what was the ROI and actual operating cost? | answered | 2.0010990420298621, 162732.63 | Beauty New Product Launch, 2026-08-10, 1.00109904202986211186, 162732.63 (1 row) |
| `q-2abe10703f` What was the total GMV for campaigns that started in September 2026? | answered | 3973234.47 | Electronics Anniversary Sale, 2026-09-02, 227761.04 (6 rows) |
| `q-28d5d41079` What was the average conversion rate for the Fresh Grocery campaigns that started in Q3 2026? | answered | 0.08085170194003527337, 0.08225882352941176471 | 0.08225882352941176471 (1 row) |
| `q-1f737aa58d` Which finished campaign that started in June 2026 had the highest ROI? | answered | Consumer Electronics, Electronics Summer Sale, 2026-06-10, 3.2497912569025250 | Electronics Summer Sale, 2026-06-10, 2.2497912569025250 (1 row) |
| `q-e9a1a5276e` For the next sale like last Beauty Black Friday, should we change the serum and sheet-mask offers, and what results in the review make the case? | answered | Beauty Black Friday Live, 2025-11-18, Sheet Mask, 470, 131623.43, 45439.53, 0.3452 | Beauty Black Friday Live, 2025-11-18, Serum, 796, 110119.84, 64158.80, 0.1972, 0.4221, 0.0943 (2 rows) |
| `q-e6ba74bc07` For the next Home Anniversary Sale, what should we change to close the sales gap without losing what worked in the 2026 campaign? | answered | Home Anniversary Sale, 2026-09-04, 766083.21, 0.07964285714285714286, 10978, 89174.11, 80775.37 | Home Anniversary Sale, 2026-09-04, 2026-09-24, finished, 89174.11, 80775.37, 766083.21, 0.07964285714285714286, 10978, Spin Mop, 724, 165723.20, 0.1990, 0.1749, … (6 rows) |
| `q-cd28b24091` Why did Grocery Black Friday Live hit its GMV and audience targets but miss conversion and go over budget? | answered | Grocery Black Friday Live, 2025-11-12, 28866.58, 34027.23, 181056.65, 0.08838571428571428571, 1960 | Grocery Black Friday Live, 2025-11-12, 28866.58, 34027.23, 5160.65, 181056.65, 16069, 0.08838571428571428571, 47.9267857142857143, 13.2017857142857143, 1032.392 … (1 row) |
| `q-40cb4f5cea` How did the product mix and returns affect the Black Friday grocery livestream’s results against its targets? | answered | Grocery Black Friday Live, 2025-11-12, 181056.65, 0.08838571428571428571, Fresh Cherries, 38189.56, 20097.08, 0.0147 | Grocery Black Friday Live, 2025-11-12, 28866.58, 34027.23, 5160.65, Fresh Cherries, 224, 38189.56, 20097.08, 0.0147, 0.1881 (6 rows) |
| `q-9abc436ce5` Why did the Feb 14 Beauty Fan Appreciation Day meet its sales and audience targets, and which product did most of the work? | answered | 766115.13, 0.08743243243243243243, 6496, Eyeshadow Palette, 4808, 507384.63, 199336.07 | Beauty Fan Appreciation Day, 2026-02-14, 144354.08, 155117.55, 766115.13, 66618, 0.08743243243243243243, 13.9640540540540541, Eyeshadow Palette, 4808, 507384.63 … (1 row) |
| `q-09f63cace2` How did the Feb 14 Beauty Fan Appreciation Day end up on ROI and operating cost versus plan, and what should we check before the next one? | answered | 144354.08, 155117.55, 247515.36, 1.5956631599712605, 0.07456297736787211002 | Beauty Fan Appreciation Day, 2026-02-14, 144354.08, 155117.55, 766115.13, 610997.58, 3.9389326352820812 (1 row) |
| `q-b57ed61f60` Why did the Jan 18 Home New Product Launch meet its sales and conversion targets but miss the viewer and cost targets? | answered | Home New Product Launch, 2026-01-18, 587611.94, 0.08822631578947368421, 8250, 113043.09, 138343.78 | Home New Product Launch, 2026-01-18, 587611.94, 0.08822631578947368421, 49172, 113043.09, 138343.78 (1 row) |
| `q-7cb370d465` And the one before that? | refused | Home New Product Launch, 2026-03-01, 2026-03-17 | – (no rows) |
| `q-c6e9e26d00` And the Beauty one? | answered | Beauty Black Friday Live, 2025-11-18, 2025-12-14 | Beauty Weekly Live Special, 2026-09-28, running, 80174.56, NULL, 518082.35, 44584, 0.07366666666666666667 (11 rows) |

## Latency and cost

| | p50 | p95 | max |
|---|---|---|---|
| ms a question | 3522 | 6298 | 7043 |
| embedding, ms | 196 | 310 | 526 |
| schema, names and examples, ms | 52 | 64 | 70 |
| writing queries, ms | 1855 | 4080 | 4932 |
| running queries, ms | 11 | 71 | 98 |
| explaining, ms | 1066 | 2093 | 2248 |
| model calls | 2 | 3 | 3 |
| queries run | 1 | 2 | 2 |

$0.0131 in all, $0.00034 a question; input 2413 and output 190 tokens a question on average.

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
