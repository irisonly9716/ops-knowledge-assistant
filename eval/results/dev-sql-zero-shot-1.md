# Text-to-SQL evaluation run: dev-sql-zero-shot-1

39 questions with a gold SQL from `eval/dev.jsonl`. LangChain agent (zero-shot), gpt-6-luna, at most 8 model calls and 120 s a question. Statuses: answered 38, stopped 1. 469 s in all.

## Execution accuracy

The last query's rows against the gold result: same number of rows, and each holds its gold row's numbers (within 0.5%), or all its values when it has no numbers. Strict: all of the gold row's values, names and dates included.

| Questions | n | Execution accuracy | Strict | Gold numbers found |
|---|---|---|---|---|
| **data: the number is the answer** | 27 | 66.7% | 55.6% | 80.0% |
| both: numbers that support an answer | 12 | 58.3% | 50.0% | 73.3% |
| data, direct | 8 | 75.0% | 75.0% | 81.2% |
| data, paraphrase | 3 | 66.7% | 66.7% | 66.7% |
| data, colloquial | 4 | 75.0% | 50.0% | 100.0% |
| data, vague | 9 | 55.6% | 33.3% | 75.0% |
| data, keyword | 3 | 66.7% | 66.7% | 83.3% |
| data, single_table | 5 | 100.0% | 100.0% | 100.0% |
| data, multi_table | 10 | 80.0% | 60.0% | 85.0% |
| data, time_range | 5 | 60.0% | 60.0% | 70.0% |
| data, multi_turn | 2 | 0.0% | 0.0% | – |
| data, asked by staff | 15 | 73.3% | 60.0% | 80.0% |
| data, asked by manager | 5 | 80.0% | 60.0% | 90.0% |
| data, asked by executive | 7 | 42.9% | 42.9% | 70.0% |

## The asker's department

Final queries for staff and managers that don't name the asker's department: 0 of 31. Read from the text of the query; the filter that counts is the one code adds and row-level security enforces.

## Wrong

| Question | Status | Gold, first row | Last query, first row |
|---|---|---|---|
| `q-123c6ee65c` What was the conversion rate for our most recent product launch? | answered | Electronics New Product Launch, 2026-09-29, 2026-10-13, 0.08736000000000000000 | Electronics New Product Launch, 2026-09-29, 2026-10-13, 0.0369 (1 row) |
| `q-91b4968461` Which finished electronics campaign had our worst ROI, and what was it? | answered | Electronics Fan Appreciation Day, 2026-08-20, 2026-09-28, 1.1434101017208975 | Electronics Back to School, 840079.00, 201833.91, 3.1622292309552939 (1 row) |
| `q-2d8f4b1df2` What was the peak live audience for our summer electronics sale? | answered | Electronics Summer Sale, 2026-06-10, 2026-07-14, 15568 | 23, Electronics Anniversary Sale, 2172 (13 rows) |
| `q-db8a36c5f0` What was the ROI for the September 2026 grocery anniversary campaign? | answered | Grocery Anniversary Sale, 2026-09-10, 1.9078150762760911 | Grocery Anniversary Sale, 2026-09-10, 2026-09-30, 119412.91, 769507.87, 544.4092770203824700 (1 row) |
| `q-2c510597be` For the Beauty New Product Launch, what was the ROI and actual operating cost? | answered | 2.0010990420298621, 162732.63 | Beauty New Product Launch, 162732.63, 1107942.63, 580.8361850969900800 (1 row) |
| `q-28d5d41079` What was the average conversion rate for the Fresh Grocery campaigns that started in Q3 2026? | answered | 0.08085170194003527337, 0.08225882352941176471 | 0.08225882352941176471 (1 row) |
| `q-1f737aa58d` Which finished campaign that started in June 2026 had the highest ROI? | answered | Consumer Electronics, Electronics Summer Sale, 2026-06-10, 3.2497912569025250 | Electronics Summer Sale, 2026-06-10, finished, 186748.69, 8.1968381143664247 (1 row) |
| `q-e6ba74bc07` For the next Home Anniversary Sale, what should we change to close the sales gap without losing what worked in the 2026 campaign? | stopped | Home Anniversary Sale, 2026-09-04, 766083.21, 0.07964285714285714286, 10978, 89174.11, 80775.37 | 40, Home New Product Launch, 2026-01-08, 2026-02-16, 40, 686958.86, 17173.97, 0.0853, 1519.0, 59.29, 12.77, 602358.96, 4477, 0.8592, 0.0467 (11 rows) |
| `q-a5902b702d` For the next Electronics Summer Sale, what should we change about traffic and spending plans, based on the 2026 post-campaign review and the campaign results? | answered | Electronics Summer Sale, 2026-06-10, 1717497.47, 0.08010000000000000000, 15568, 182684.30, 186748.69 | Smartwatch, 2498, 630313.97, 370197.23, 0.5873, 0.1297, 0.2638, 0.1896 (4 rows) |
| `q-cd28b24091` Why did Grocery Black Friday Live hit its GMV and audience targets but miss conversion and go over budget? | answered | Grocery Black Friday Live, 2025-11-12, 28866.58, 34027.23, 181056.65, 0.08838571428571428571, 1960 | 181056.65, 16069, 573.8928571428571429, 0.08838571428571428571, 11.2674497479619142, 0.0112, 0.1442, 28866.58, 34027.23, 5160.65, 17.9 (1 row) |
| `q-40cb4f5cea` How did the product mix and returns affect the Black Friday grocery livestream’s results against its targets? | answered | Grocery Black Friday Live, 2025-11-12, 181056.65, 0.08838571428571428571, Fresh Cherries, 38189.56, 20097.08, 0.0147 | 27, Grocery Black Friday Live, Fresh Grocery, 2025-11-12, 2025-12-09, finished, 28866.58, 34027.23, 6, [{'profit': 20097.08, 'product': 'Fresh Cherries', 'units … (1 row) |
| `q-09f63cace2` How did the Feb 14 Beauty Fan Appreciation Day end up on ROI and operating cost versus plan, and what should we check before the next one? | answered | 144354.08, 155117.55, 247515.36, 1.5956631599712605, 0.07456297736787211002 | Beauty Fan Appreciation Day, 2026-02-14, finished, True, True, 144354.08, 155117.55, 10763.47, 7.46, 766115.13, 610997.58, 393.89, 0.08743243243243243243, 6496, … (1 row) |
| `q-7cb370d465` And the one before that? | answered | Home New Product Launch, 2026-03-01, 2026-03-17 | 11, Beauty Weekly Live Special, Beauty, 2026-09-28, 2026-11-03, running (1 row) |
| `q-c6e9e26d00` And the Beauty one? | answered | Beauty Black Friday Live, 2025-11-18, 2025-12-14 | 1, Beauty Black Friday Live, Beauty, Beauty, 2025-11-18, 2025-12-14, finished, 71061.53, 73552.32, 600441.45, 0.08617037037037037037, 6488 (11 rows) |

## Latency and cost

| | p50 | p95 | max |
|---|---|---|---|
| ms a question | 8906 | 27581 | 34418 |
| model calls | 4 | 8 | 8 |
| queries run | 1 | 6 | 7 |

$0.0396 in all, $0.00102 a question; input 5631 and output 904 tokens a question on average.

## Instructions

As for a staff member in Beauty:

```
You answer questions about an operations team's livestream campaigns by querying its PostgreSQL database.

The question comes from a staff in the Beauty department, who may only see that department's data: every query must keep to the Beauty department's campaigns. Today is 2026-10-15.

First list the tables, then read the columns of the ones that look relevant. Then write one PostgreSQL SELECT query that answers the question and run it. If it fails, read the error, fix the query and run it again. Only ever write SELECT queries.

Return every row the question asks for; add a LIMIT only when the question asks for a number of them. When the query has run, answer the question in a sentence or two from its result.
```
