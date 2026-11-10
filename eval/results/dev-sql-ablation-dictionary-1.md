# Text-to-SQL evaluation run: dev-sql-ablation-dictionary-1

39 questions with a gold SQL from `eval/dev.jsonl`. Fixed pipeline (zero-shot + data dictionary), writing with gpt-6-luna and explaining with gpt-6-luna, 12 s a question, queries run as the tables' owner. Statuses: answered 36, refused 2, failed 1. 137 s in all.

## Execution accuracy

The last query's rows against the gold result: same number of rows, and each holds its gold row's numbers (within 0.5%), or all its values when it has no numbers. Strict: all of the gold row's values, names and dates included.

| Questions | n | Execution accuracy | Strict | Gold numbers found |
|---|---|---|---|---|
| **data: the number is the answer** | 27 | 70.4% | 55.6% | 72.2% |
| both: numbers that support an answer | 12 | 50.0% | 50.0% | 73.8% |
| data, direct | 8 | 62.5% | 62.5% | 62.5% |
| data, paraphrase | 3 | 66.7% | 66.7% | 66.7% |
| data, colloquial | 4 | 75.0% | 75.0% | 75.0% |
| data, vague | 9 | 77.8% | 33.3% | 77.8% |
| data, keyword | 3 | 66.7% | 66.7% | 83.3% |
| data, single_table | 5 | 100.0% | 100.0% | 100.0% |
| data, multi_table | 10 | 80.0% | 80.0% | 85.0% |
| data, time_range | 5 | 40.0% | 40.0% | 40.0% |
| data, multi_turn | 2 | 0.0% | 0.0% | 0.0% |
| data, asked by staff | 15 | 86.7% | 60.0% | 86.7% |
| data, asked by manager | 5 | 80.0% | 80.0% | 90.0% |
| data, asked by executive | 7 | 28.6% | 28.6% | 28.6% |

## The asker's department

Answered queries for staff and managers that ran with the department filter the code adds: 31 of 31.

## Writing and correcting

Answered on the first query: 36; after correcting: none. Of the corrected ones, 0 of 0 were right.

Queries that failed: 1 (GroupingError 1).
- `q-e6ba74bc07`: GroupingError: column "c.name" must appear in the GROUP BY clause or be used in an aggregate function LINE 1: WITH campaign_results AS (SELECT c.campaign_id, c.

## Wrong

| Question | Status | Gold, first row | Last query, first row |
|---|---|---|---|
| `q-91b4968461` Which finished electronics campaign had our worst ROI, and what was it? | answered | Electronics Fan Appreciation Day, 2026-08-20, 2026-09-28, 1.1434101017208975 | Electronics Back to School, 2026-08-10, 840079.00, 201833.91, 3.1622292309552939 (1 row) |
| `q-db8a36c5f0` What was the ROI for the September 2026 grocery anniversary campaign? | answered | Grocery Anniversary Sale, 2026-09-10, 1.9078150762760911 | Grocery Anniversary Sale, 2026-09-10, 0.90781507627609108596 (1 row) |
| `q-2c510597be` For the Beauty New Product Launch, what was the ROI and actual operating cost? | answered | 2.0010990420298621, 162732.63 | Beauty New Product Launch, 2026-08-10, 162732.63, 1.00109904202986211186 (1 row) |
| `q-2abe10703f` What was the total GMV for campaigns that started in September 2026? | answered | 3973234.47 | Electronics Anniversary Sale, 2026-09-02, 227761.04 (6 rows) |
| `q-28d5d41079` What was the average conversion rate for the Fresh Grocery campaigns that started in Q3 2026? | answered | 0.08085170194003527337, 0.08225882352941176471 | Grocery Back to School, 2026-08-06, 0.09569629629629629630 (3 rows) |
| `q-1f737aa58d` Which finished campaign that started in June 2026 had the highest ROI? | answered | Consumer Electronics, Electronics Summer Sale, 2026-06-10, 3.2497912569025250 | Electronics Summer Sale, 2026-06-10, 8.1968381143664247 (1 row) |
| `q-e6ba74bc07` For the next Home Anniversary Sale, what should we change to close the sales gap without losing what worked in the 2026 campaign? | failed | Home Anniversary Sale, 2026-09-04, 766083.21, 0.07964285714285714286, 10978, 89174.11, 80775.37 | – (no rows) |
| `q-cd28b24091` Why did Grocery Black Friday Live hit its GMV and audience targets but miss conversion and go over budget? | answered | Grocery Black Friday Live, 2025-11-12, 28866.58, 34027.23, 181056.65, 0.08838571428571428571, 1960 | Grocery Black Friday Live, 2025-11-12, 28866.58, 34027.23, 1086339.90, 96414, 0.08838571428571428571, 0.15796666666666666667, 32480, 4108756.68, 1919981.56, 0.0 … (1 row) |
| `q-40cb4f5cea` How did the product mix and returns affect the Black Friday grocery livestream’s results against its targets? | answered | Grocery Black Friday Live, 2025-11-12, 181056.65, 0.08838571428571428571, Fresh Cherries, 38189.56, 20097.08, 0.0147 | Grocery Black Friday Live, 2025-11-12, 28866.58, 34027.23, 5160.65, 146741.31, 68570.77, 114.6110, 0.07244421446149008756, Fresh Cherries, 224, 38189.56, 0.2602 … (6 rows) |
| `q-9abc436ce5` Why did the Feb 14 Beauty Fan Appreciation Day meet its sales and audience targets, and which product did most of the work? | answered | 766115.13, 0.08743243243243243243, 6496, Eyeshadow Palette, 4808, 507384.63, 199336.07 | Beauty Fan Appreciation Day, 2026-02-14, 144354.08, 155117.55, 766115.13, 66618, 0.08743243243243243243, 54.7856756756756757, 3560.5405405405405405, Eyeshadow P … (1 row) |
| `q-09f63cace2` How did the Feb 14 Beauty Fan Appreciation Day end up on ROI and operating cost versus plan, and what should we check before the next one? | answered | 144354.08, 155117.55, 247515.36, 1.5956631599712605, 0.07456297736787211002 | Beauty Fan Appreciation Day, 2026-02-14, 144354.08, 155117.55, 10763.47, 92397.81, 0.59566315997126050534, 766115.13, 66618, 54.7856756756756757, 0.087432432432 … (1 row) |
| `q-b57ed61f60` Why did the Jan 18 Home New Product Launch meet its sales and conversion targets but miss the viewer and cost targets? | answered | Home New Product Launch, 2026-01-18, 587611.94, 0.08822631578947368421, 8250, 113043.09, 138343.78 | Home New Product Launch, 2026-01-18, 113043.09, 138343.78, 587611.94, 0.08822631578947368421, 49172, 472533.34, 3096, 0.55174000000000000000, 0.1129600000000000 … (1 row) |
| `q-7cb370d465` And the one before that? | refused | Home New Product Launch, 2026-03-01, 2026-03-17 | – (no rows) |
| `q-c6e9e26d00` And the Beauty one? | refused | Beauty Black Friday Live, 2025-11-18, 2025-12-14 | – (no rows) |

## Latency and cost

| | p50 | p95 | max |
|---|---|---|---|
| ms a question | 2898 | 7504 | 8150 |
| embedding, ms | 0 | 0 | 0 |
| schema, names and examples, ms | 6 | 12 | 85 |
| writing queries, ms | 1709 | 5870 | 6005 |
| running queries, ms | 11 | 32 | 70 |
| explaining, ms | 1118 | 2550 | 2566 |
| model calls | 2 | 2 | 2 |
| queries run | 1 | 1 | 1 |

$0.0123 in all, $0.00031 a question; input 1991 and output 230 tokens a question on average.

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
