# Text-to-SQL evaluation run: dev-sql-zero-shot-2

39 questions with a gold SQL from `eval/dev.jsonl`. LangChain agent (zero-shot), gpt-6-luna, at most 8 model calls and 120 s a question. Statuses: answered 39. 469 s in all.

## Execution accuracy

The last query's rows against the gold result: same number of rows, and each holds its gold row's numbers (within 0.5%), or all its values when it has no numbers. Strict: all of the gold row's values, names and dates included.

| Questions | n | Execution accuracy | Strict | Gold numbers found |
|---|---|---|---|---|
| **data: the number is the answer** | 27 | 70.4% | 55.6% | 80.0% |
| both: numbers that support an answer | 12 | 58.3% | 50.0% | 84.2% |
| data, direct | 8 | 75.0% | 75.0% | 81.2% |
| data, paraphrase | 3 | 66.7% | 33.3% | 66.7% |
| data, colloquial | 4 | 75.0% | 75.0% | 100.0% |
| data, vague | 9 | 66.7% | 33.3% | 75.0% |
| data, keyword | 3 | 66.7% | 66.7% | 83.3% |
| data, single_table | 5 | 100.0% | 100.0% | 100.0% |
| data, multi_table | 10 | 80.0% | 60.0% | 85.0% |
| data, time_range | 5 | 60.0% | 60.0% | 70.0% |
| data, multi_turn | 2 | 0.0% | 0.0% | – |
| data, asked by staff | 15 | 80.0% | 60.0% | 80.0% |
| data, asked by manager | 5 | 80.0% | 60.0% | 90.0% |
| data, asked by executive | 7 | 42.9% | 42.9% | 70.0% |

## The asker's department

Final queries for staff and managers that don't name the asker's department: 0 of 32. Read from the text of the query; the filter that counts is the one code adds and row-level security enforces.

## Wrong

| Question | Status | Gold, first row | Last query, first row |
|---|---|---|---|
| `q-123c6ee65c` What was the conversion rate for our most recent product launch? | answered | Electronics New Product Launch, 2026-09-29, 2026-10-13, 0.08736000000000000000 | Electronics New Product Launch, 2026-09-29, 2026-10-13, 0.0369 (1 row) |
| `q-91b4968461` Which finished electronics campaign had our worst ROI, and what was it? | answered | Electronics Fan Appreciation Day, 2026-08-20, 2026-09-28, 1.1434101017208975 | Electronics Back to School, 840079.00, 201833.91, 3.1622292309552939 (1 row) |
| `q-db8a36c5f0` What was the ROI for the September 2026 grocery anniversary campaign? | answered | Grocery Anniversary Sale, 2026-09-10, 1.9078150762760911 | Grocery Anniversary Sale, 544.41 (1 row) |
| `q-2c510597be` For the Beauty New Product Launch, what was the ROI and actual operating cost? | answered | 2.0010990420298621, 162732.63 | Beauty New Product Launch, 1107942.63, 162732.63, 580.84 (1 row) |
| `q-28d5d41079` What was the average conversion rate for the Fresh Grocery campaigns that started in Q3 2026? | answered | 0.08085170194003527337, 0.08225882352941176471 | 0.08225882352941176471 (1 row) |
| `q-1f737aa58d` Which finished campaign that started in June 2026 had the highest ROI? | answered | Consumer Electronics, Electronics Summer Sale, 2026-06-10, 3.2497912569025250 | 19, Electronics Summer Sale, 2026-06-10, 2026-07-14, 186748.69, 1717497.47, 8.1968381143664247 (6 rows) |
| `q-e6ba74bc07` For the next Home Anniversary Sale, what should we change to close the sales gap without losing what worked in the 2026 campaign? | answered | Home Anniversary Sale, 2026-09-04, 766083.21, 0.07964285714285714286, 10978, 89174.11, 80775.37 | Home Anniversary Sale, 21, 766083.21, 36480.152857142857, 3203.7142857142857143, 0.07964285714285714286, 47.2838095238095238, 10978, Home Summer Sale, 45, 16779 … (1 row) |
| `q-cd28b24091` Why did Grocery Black Friday Live hit its GMV and audience targets but miss conversion and go over budget? | answered | Grocery Black Friday Live, 2025-11-12, 28866.58, 34027.23, 181056.65, 0.08838571428571428571, 1960 | Grocery Black Friday Live, 28866.58, 34027.23, 5160.65, 17.88, 28, 181056.65, 573.89, 1030, 0.0884, 47.93, 13.20, 6, 146741.31, 1160, 0.1580, 0.3262, 0.0807, 68 … (1 row) |
| `q-40cb4f5cea` How did the product mix and returns affect the Black Friday grocery livestream’s results against its targets? | answered | Grocery Black Friday Live, 2025-11-12, 181056.65, 0.08838571428571428571, Fresh Cherries, 38189.56, 20097.08, 0.0147 | Grocery Black Friday Live, 28866.58, 34027.23, 5160.65, 146741.31, 68570.77, 0.07244421446149008756, 10630.558932, 181056.65, 16069, 0.08838571428571428571, 47. … (1 row) |
| `q-9abc436ce5` Why did the Feb 14 Beauty Fan Appreciation Day meet its sales and audience targets, and which product did most of the work? | answered | 766115.13, 0.08743243243243243243, 6496, Eyeshadow Palette, 4808, 507384.63, 199336.07 | Beauty Fan Appreciation Day, 2026-02-14, 2026-03-22, 766115.13, 1800.49, 2910, 6496, 0.0874, 13.96, 608886.72, [{'sales': 507384.63, 'units': 4808, 'profit': 19 … (1 row) |
| `q-09f63cace2` How did the Feb 14 Beauty Fan Appreciation Day end up on ROI and operating cost versus plan, and what should we check before the next one? | answered | 144354.08, 155117.55, 247515.36, 1.5956631599712605, 0.07456297736787211002 | 3, Beauty Fan Appreciation Day, 2026-02-14, 2026-03-22, finished, 144354.08, 155117.55, 10763.47, 0.07456297736787211002, 766115.13, 3.9389326352820812, 608886. … (1 row) |
| `q-7cb370d465` And the one before that? | answered | Home New Product Launch, 2026-03-01, 2026-03-17 | 11, Beauty Weekly Live Special, 2026-09-28, 2026-11-03, running, Beauty, Jonathan Wilkerson (1 row) |
| `q-c6e9e26d00` And the Beauty one? | answered | Beauty Black Friday Live, 2025-11-18, 2025-12-14 | Beauty, 1, Beauty Black Friday Live, Beauty, 2025-11-18, 2025-12-14, finished, True, True, 71061.53, 73552.32, 27, 600441.45, 0.08617037037037037037, 55668, 44. … (11 rows) |

## Latency and cost

| | p50 | p95 | max |
|---|---|---|---|
| ms a question | 8353 | 33909 | 34017 |
| model calls | 4 | 7 | 8 |
| queries run | 1 | 3 | 4 |

$0.0362 in all, $0.00093 a question; input 4617 and output 933 tokens a question on average.

## Instructions

As for a staff member in Beauty:

```
You answer questions about an operations team's livestream campaigns by querying its PostgreSQL database.

The question comes from a staff in the Beauty department, who may only see that department's data: every query must keep to the Beauty department's campaigns. Today is 2026-10-15.

First list the tables, then read the columns of the ones that look relevant. Then write one PostgreSQL SELECT query that answers the question and run it. If it fails, read the error, fix the query and run it again. Only ever write SELECT queries.

Return every row the question asks for; add a LIMIT only when the question asks for a number of them. When the query has run, answer the question in a sentence or two from its result.
```
