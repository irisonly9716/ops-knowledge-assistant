# Text-to-SQL evaluation run: dev-sql-ablation-zero-shot-2

39 questions with a gold SQL from `eval/dev.jsonl`. Fixed pipeline (zero-shot), writing with gpt-6-luna and explaining with gpt-6-luna, 12 s a question, queries run as the tables' owner. Statuses: answered 38, refused 1. 118 s in all.

## Execution accuracy

The last query's rows against the gold result: same number of rows, and each holds its gold row's numbers (within 0.5%), or all its values when it has no numbers. Strict: all of the gold row's values, names and dates included.

| Questions | n | Execution accuracy | Strict | Gold numbers found |
|---|---|---|---|---|
| **data: the number is the answer** | 27 | 51.9% | 44.4% | 55.8% |
| both: numbers that support an answer | 12 | 25.0% | 25.0% | 64.3% |
| data, direct | 8 | 50.0% | 50.0% | 50.0% |
| data, paraphrase | 3 | 66.7% | 66.7% | 66.7% |
| data, colloquial | 4 | 50.0% | 50.0% | 50.0% |
| data, vague | 9 | 44.4% | 22.2% | 50.0% |
| data, keyword | 3 | 66.7% | 66.7% | 83.3% |
| data, single_table | 5 | 80.0% | 80.0% | 80.0% |
| data, multi_table | 10 | 60.0% | 60.0% | 65.0% |
| data, time_range | 5 | 40.0% | 40.0% | 40.0% |
| data, multi_turn | 2 | 0.0% | 0.0% | 0.0% |
| data, asked by staff | 15 | 53.3% | 40.0% | 53.3% |
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
| `q-cd428e41fe` How much GMV did last Black Friday's electronics livestream bring in? | answered | Electronics Black Friday Live, 2025-11-16, 2025-12-12, 1043330.18 | – (0 rows) |
| `q-123c6ee65c` What was the conversion rate for our most recent product launch? | answered | Electronics New Product Launch, 2026-09-29, 2026-10-13, 0.08736000000000000000 | – (0 rows) |
| `q-2d8f4b1df2` What was the peak live audience for our summer electronics sale? | answered | Electronics Summer Sale, 2026-06-10, 2026-07-14, 15568 | – (0 rows) |
| `q-b66ecb8a0e` What was the average conversion rate for last Black Friday's grocery live? | answered | 0.08838571428571428571 | – (0 rows) |
| `q-42f95e9ad2` Which product brought in the most sales revenue during the 2026 Grocery Summer Sale, and how many units did it sell? | answered | Mixed Nut Gift Box, 115497.33, 417 | – (0 rows) |
| `q-db8a36c5f0` What was the ROI for the September 2026 grocery anniversary campaign? | answered | Grocery Anniversary Sale, 2026-09-10, 1.9078150762760911 | – (0 rows) |
| `q-faeada22c8` which region spent the most on our back-to-school grocery live, and how much? | answered | Southwest, 9539.90 | – (0 rows) |
| `q-2c510597be` For the Beauty New Product Launch, what was the ROI and actual operating cost? | answered | 2.0010990420298621, 162732.63 | Beauty New Product Launch, 2026-08-10, 0.04378415510844968520, 162732.63 (1 row) |
| `q-2abe10703f` What was the total GMV for campaigns that started in September 2026? | answered | 3973234.47 | Electronics Anniversary Sale, 2026-09-02, 227761.04 (6 rows) |
| `q-28d5d41079` What was the average conversion rate for the Fresh Grocery campaigns that started in Q3 2026? | answered | 0.08085170194003527337, 0.08225882352941176471 | Grocery Back to School, 2026-08-06, 0.09569629629629629630 (3 rows) |
| `q-1f737aa58d` Which finished campaign that started in June 2026 had the highest ROI? | answered | Consumer Electronics, Electronics Summer Sale, 2026-06-10, 3.2497912569025250 | Electronics Summer Sale, 2026-06-10, 8.1968381143664247 (1 row) |
| `q-e6ba74bc07` For the next Home Anniversary Sale, what should we change to close the sales gap without losing what worked in the 2026 campaign? | answered | Home Anniversary Sale, 2026-09-04, 766083.21, 0.07964285714285714286, 10978, 89174.11, 80775.37 | 50, Home Anniversary Sale, 2026-09-04, 89174.11, 80775.37, 4596499.26, 9739899.54, 58023, 4518500.07, 0.14553333333333333333, 0.43176666666666666667, 0.18555000 … (1 row) |
| `q-a5902b702d` For the next Electronics Summer Sale, what should we change about traffic and spending plans, based on the 2026 post-campaign review and the campaign results? | answered | Electronics Summer Sale, 2026-06-10, 1717497.47, 0.08010000000000000000, 15568, 182684.30, 186748.69 | 19, Electronics Summer Sale, 2026-06-10, 2026-07-14, finished, True, True, 182684.30, 186748.69, 4064.39, 150320, 4294.8571428571428571, 15568, 1717497.47, 1530 … (1 row) |
| `q-b482d79dd3` When we build the next summer electronics lineup, which products should get a bigger role or a closer look, and what results in the 2026 Summer Sale review support that? | answered | Electronics Summer Sale, 2026-06-10, Wireless Earbuds, 244397.60, 135535.37, 0.5546, 0.1079 | – (0 rows) |
| `q-cd28b24091` Why did Grocery Black Friday Live hit its GMV and audience targets but miss conversion and go over budget? | answered | Grocery Black Friday Live, 2025-11-12, 28866.58, 34027.23, 181056.65, 0.08838571428571428571, 1960 | Grocery Black Friday Live, 2025-11-12, 181056.65, 16069, 0.08838571428571428571, 28866.58, 34027.23, 5160.65, 1160, 146741.31, 68570.77, 0.08068333333333333333, … (1 row) |
| `q-40cb4f5cea` How did the product mix and returns affect the Black Friday grocery livestream’s results against its targets? | answered | Grocery Black Friday Live, 2025-11-12, 181056.65, 0.08838571428571428571, Fresh Cherries, 38189.56, 20097.08, 0.0147 | Grocery Black Friday Live, 2025-11-12, 28866.58, 34027.23, 5160.65, Fresh Cherries, 224, 38189.56, 20097.08, 0.5262, 0.1881, 0.0147 (6 rows) |
| `q-9abc436ce5` Why did the Feb 14 Beauty Fan Appreciation Day meet its sales and audience targets, and which product did most of the work? | answered | 766115.13, 0.08743243243243243243, 6496, Eyeshadow Palette, 4808, 507384.63, 199336.07 | Beauty Fan Appreciation Day, 2026-02-14, 144354.08, 155117.55, 766115.13, 2910, 6496, 18773231.31, 177896, Eyeshadow Palette, 507384.63, 4808 (1 row) |
| `q-09f63cace2` How did the Feb 14 Beauty Fan Appreciation Day end up on ROI and operating cost versus plan, and what should we check before the next one? | answered | 144354.08, 155117.55, 247515.36, 1.5956631599712605, 0.07456297736787211002 | Beauty Fan Appreciation Day, 2026-02-14, 144354.08, 155117.55, 10763.47, 9158068.32, 3064460.52, 59.0395369189366387 (1 row) |
| `q-b57ed61f60` Why did the Jan 18 Home New Product Launch meet its sales and conversion targets but miss the viewer and cost targets? | answered | Home New Product Launch, 2026-01-18, 587611.94, 0.08822631578947368421, 8250, 113043.09, 138343.78 | Home New Product Launch, 2026-01-18, 113043.09, 138343.78, 2026-01-18, 44976.27, 0.0881, 3385, 6413 (19 rows) |
| `q-0f544827a5` For the Jan 18 launch, how did the product mix help sales land on target, and what product issues still need attention? | answered | Home New Product Launch, 2026-01-18, 587611.94, Paper Towels, 175001.62, 85811.02, 0.1030 | Home New Product Launch, 2026-01-18, finished, True, True, 113043.09, 138343.78, 587611.94, 5, 3096, 472533.34, 207841.73, 0.61121584422804960175, 0.11104657769 … (1 row) |
| `q-7cb370d465` And the one before that? | refused | Home New Product Launch, 2026-03-01, 2026-03-17 | – (no rows) |
| `q-c6e9e26d00` And the Beauty one? | answered | Beauty Black Friday Live, 2025-11-18, 2025-12-14 | Beauty Fan Appreciation Day, 2026-02-14, finished, 3064460.52, 266472, 0.08743243243243243243, 219336, 22528808.64, 9158068.32, 0.10672500000000000000 (11 rows) |

## Latency and cost

| | p50 | p95 | max |
|---|---|---|---|
| ms a question | 2909 | 5348 | 5839 |
| embedding, ms | 0 | 0 | 0 |
| schema, names and examples, ms | 1 | 4 | 38 |
| writing queries, ms | 1782 | 4267 | 4668 |
| running queries, ms | 13 | 50 | 102 |
| explaining, ms | 1010 | 2006 | 2113 |
| model calls | 2 | 2 | 2 |
| queries run | 1 | 1 | 1 |

$0.0081 in all, $0.00021 a question; input 1118 and output 191 tokens a question on average.

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
