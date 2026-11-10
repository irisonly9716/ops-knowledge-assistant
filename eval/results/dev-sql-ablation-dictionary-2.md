# Text-to-SQL evaluation run: dev-sql-ablation-dictionary-2

39 questions with a gold SQL from `eval/dev.jsonl`. Fixed pipeline (zero-shot + data dictionary), writing with gpt-6-luna and explaining with gpt-6-luna, 12 s a question, queries run as the tables' owner. Statuses: answered 38, refused 1. 123 s in all.

## Execution accuracy

The last query's rows against the gold result: same number of rows, and each holds its gold row's numbers (within 0.5%), or all its values when it has no numbers. Strict: all of the gold row's values, names and dates included.

| Questions | n | Execution accuracy | Strict | Gold numbers found |
|---|---|---|---|---|
| **data: the number is the answer** | 27 | 70.4% | 55.6% | 75.0% |
| both: numbers that support an answer | 12 | 25.0% | 25.0% | 78.2% |
| data, direct | 8 | 62.5% | 62.5% | 62.5% |
| data, paraphrase | 3 | 66.7% | 66.7% | 66.7% |
| data, colloquial | 4 | 75.0% | 75.0% | 75.0% |
| data, vague | 9 | 77.8% | 33.3% | 87.5% |
| data, keyword | 3 | 66.7% | 66.7% | 83.3% |
| data, single_table | 5 | 100.0% | 100.0% | 100.0% |
| data, multi_table | 10 | 80.0% | 80.0% | 85.0% |
| data, time_range | 5 | 40.0% | 40.0% | 40.0% |
| data, multi_turn | 2 | 0.0% | 0.0% | 0.0% |
| data, asked by staff | 15 | 86.7% | 60.0% | 86.7% |
| data, asked by manager | 5 | 80.0% | 80.0% | 90.0% |
| data, asked by executive | 7 | 28.6% | 28.6% | 33.3% |

## The asker's department

Answered queries for staff and managers that ran with the department filter the code adds: 32 of 32.

## Writing and correcting

Answered on the first query: 38; after correcting: none. Of the corrected ones, 0 of 0 were right.

Queries that failed: 0.

## Wrong

| Question | Status | Gold, first row | Last query, first row |
|---|---|---|---|
| `q-91b4968461` Which finished electronics campaign had our worst ROI, and what was it? | answered | Electronics Fan Appreciation Day, 2026-08-20, 2026-09-28, 1.1434101017208975 | Electronics Back to School, 2026-08-10, 3.1622292309552939 (1 row) |
| `q-db8a36c5f0` What was the ROI for the September 2026 grocery anniversary campaign? | answered | Grocery Anniversary Sale, 2026-09-10, 1.9078150762760911 | Grocery Anniversary Sale, 2026-09-10, 0.90781507627609108596 (1 row) |
| `q-2c510597be` For the Beauty New Product Launch, what was the ROI and actual operating cost? | answered | 2.0010990420298621, 162732.63 | Beauty New Product Launch, 2026-08-10, 5.8083618509699008, 162732.63 (1 row) |
| `q-2abe10703f` What was the total GMV for campaigns that started in September 2026? | answered | 3973234.47 | Electronics Anniversary Sale, 2026-09-02, 227761.04 (6 rows) |
| `q-28d5d41079` What was the average conversion rate for the Fresh Grocery campaigns that started in Q3 2026? | answered | 0.08085170194003527337, 0.08225882352941176471 | Grocery Back to School, 2026-08-06, 0.09569629629629629630 (3 rows) |
| `q-1f737aa58d` Which finished campaign that started in June 2026 had the highest ROI? | answered | Consumer Electronics, Electronics Summer Sale, 2026-06-10, 3.2497912569025250 | Electronics Summer Sale, 2026-06-10, 2.2497912569025250 (1 row) |
| `q-e6ba74bc07` For the next Home Anniversary Sale, what should we change to close the sales gap without losing what worked in the 2026 campaign? | answered | Home Anniversary Sale, 2026-09-04, 766083.21, 0.07964285714285714286, 10978, 89174.11, 80775.37 | Home Anniversary Sale, 2026-09-04, 2026-09-24, finished, 766083.21, 89174.11, 80775.37, 0.07964285714285714286, 3203.7142857142857143, 13.4690476190476190, 47.2 … (6 rows) |
| `q-5a69071550` When we plan another anniversary sale, how should we rethink which products get airtime given the sales and returns from the last one? | answered | Home Anniversary Sale, 2026-09-04, Spin Mop, 724, 165723.20, 86394.52, 0.5213, 0.1990 | Home Anniversary Sale, 2026-09-04, Spin Mop, 724, 165723.20, 0.1990 (6 rows) |
| `q-a5902b702d` For the next Electronics Summer Sale, what should we change about traffic and spending plans, based on the 2026 post-campaign review and the campaign results? | answered | Electronics Summer Sale, 2026-06-10, 1717497.47, 0.08010000000000000000, 15568, 182684.30, 186748.69 | 19, Electronics Summer Sale, 2026-06-10, finished, 182684.30, 186748.69, 1717497.47, 150320, 0.08010000000000000000, 8354.6000000000000000 (1 row) |
| `q-cd28b24091` Why did Grocery Black Friday Live hit its GMV and audience targets but miss conversion and go over budget? | answered | Grocery Black Friday Live, 2025-11-12, 28866.58, 34027.23, 181056.65, 0.08838571428571428571, 1960 | Grocery Black Friday Live, 2025-11-12, 28866.58, 34027.23, 181056.65, 573.8928571428571429, 0.08838571428571428571 (1 row) |
| `q-40cb4f5cea` How did the product mix and returns affect the Black Friday grocery livestream’s results against its targets? | answered | Grocery Black Friday Live, 2025-11-12, 181056.65, 0.08838571428571428571, Fresh Cherries, 38189.56, 20097.08, 0.0147 | Grocery Black Friday Live, 2025-11-12, 28866.58, 34027.23, 5160.65, 181056.65, 16069, 0.08838571428571428571, 6, 1160, 146741.31, 68570.77, 0.098802586206896551 … (1 row) |
| `q-9abc436ce5` Why did the Feb 14 Beauty Fan Appreciation Day meet its sales and audience targets, and which product did most of the work? | answered | 766115.13, 0.08743243243243243243, 6496, Eyeshadow Palette, 4808, 507384.63, 199336.07 | Beauty Fan Appreciation Day, 2026-02-14, 2026-03-22, 766115.13, 66618, 0.08743243243243243243, 13.9640540540540541, 54.7856756756756757, 3560.5405405405405405,  … (4 rows) |
| `q-09f63cace2` How did the Feb 14 Beauty Fan Appreciation Day end up on ROI and operating cost versus plan, and what should we check before the next one? | answered | 144354.08, 155117.55, 247515.36, 1.5956631599712605, 0.07456297736787211002 | Beauty Fan Appreciation Day, 2026-02-14, 1.5956631599712605, 144354.08, 155117.55, 10763.47 (1 row) |
| `q-b57ed61f60` Why did the Jan 18 Home New Product Launch meet its sales and conversion targets but miss the viewer and cost targets? | answered | Home New Product Launch, 2026-01-18, 587611.94, 0.08822631578947368421, 8250, 113043.09, 138343.78 | Home New Product Launch, 2026-01-18, 2026-02-05, 113043.09, 138343.78, 587611.94, 49172, 0.08822631578947368421, 4744.2631578947368421, 51.2321052631578947, 17. … (1 row) |
| `q-0f544827a5` For the Jan 18 launch, how did the product mix help sales land on target, and what product issues still need attention? | answered | Home New Product Launch, 2026-01-18, 587611.94, Paper Towels, 175001.62, 85811.02, 0.1030 | Home New Product Launch, 2026-01-18, 2026-02-05, 113043.09, 138343.78, 85486.55, 637, 29449.66, Insulated Tumbler, 637, 85486.55, 0.3757, 0.1259, 0.2317, 29449. … (5 rows) |
| `q-7cb370d465` And the one before that? | refused | Home New Product Launch, 2026-03-01, 2026-03-17 | – (no rows) |
| `q-c6e9e26d00` And the Beauty one? | answered | Beauty Black Friday Live, 2025-11-18, 2025-12-14 | Beauty Weekly Live Special, 2026-09-28, 2026-11-03, running, 80174.56, NULL, 518082.35, 44584, 0, 0 (11 rows) |

## Latency and cost

| | p50 | p95 | max |
|---|---|---|---|
| ms a question | 2836 | 5258 | 6527 |
| embedding, ms | 0 | 0 | 0 |
| schema, names and examples, ms | 6 | 10 | 67 |
| writing queries, ms | 1694 | 3680 | 4643 |
| running queries, ms | 11 | 94 | 135 |
| explaining, ms | 1063 | 1858 | 1858 |
| model calls | 2 | 2 | 2 |
| queries run | 1 | 1 | 1 |

$0.0118 in all, $0.00030 a question; input 2004 and output 205 tokens a question on average.

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
