# Text-to-SQL evaluation run: dev-sql-zero-shot-1

39 questions with a gold SQL from `eval/dev.jsonl`. LangChain agent (zero-shot), gpt-6-luna, at most 8 model calls and 120 s a question. Statuses: answered 39. 469 s in all.

## Execution accuracy

The last query's rows against the gold result: same number of rows, and each holds its gold row's numbers (within 0.5%), or all its values when it has no numbers. Strict: all of the gold row's values, names and dates included.

| Questions | n | Execution accuracy | Strict | Gold numbers found |
|---|---|---|---|---|
| **data: the number is the answer** | 27 | 66.7% | 48.1% | 78.0% |
| both: numbers that support an answer | 12 | 58.3% | 41.7% | 94.4% |
| data, direct | 8 | 75.0% | 75.0% | 75.0% |
| data, paraphrase | 3 | 66.7% | 33.3% | 66.7% |
| data, colloquial | 4 | 50.0% | 50.0% | 100.0% |
| data, vague | 9 | 66.7% | 22.2% | 75.0% |
| data, keyword | 3 | 66.7% | 66.7% | 83.3% |
| data, single_table | 5 | 80.0% | 80.0% | 100.0% |
| data, multi_table | 10 | 80.0% | 60.0% | 85.0% |
| data, time_range | 5 | 60.0% | 60.0% | 60.0% |
| data, multi_turn | 2 | 0.0% | 0.0% | – |
| data, asked by staff | 15 | 73.3% | 46.7% | 80.0% |
| data, asked by manager | 5 | 80.0% | 60.0% | 90.0% |
| data, asked by executive | 7 | 42.9% | 42.9% | 60.0% |

## The asker's department

Final queries for staff and managers that don't name the asker's department: 0 of 32. Read from the text of the query; the filter that counts is the one code adds and row-level security enforces.

## Wrong

| Question | Status | Gold, first row | Last query, first row |
|---|---|---|---|
| `q-123c6ee65c` What was the conversion rate for our most recent product launch? | answered | Electronics New Product Launch, 2026-09-29, 2026-10-13, 0.08736000000000000000 | Electronics New Product Launch, 2026-09-29, 2026-10-13, 0.0369 (1 rows) |
| `q-91b4968461` Which finished electronics campaign had our worst ROI, and what was it? | answered | Electronics Fan Appreciation Day, 2026-08-20, 2026-09-28, 1.1434101017208975 | 21, Electronics Back to School, 840079.00, 201833.91, 316.2229230955293885 (1 rows) |
| `q-d812716ac4` which product sold the most units in our back to school live? | answered | Ribeye Steak, 4142 | Ribeye Steak, 4142 (3 rows) |
| `q-db8a36c5f0` What was the ROI for the September 2026 grocery anniversary campaign? | answered | Grocery Anniversary Sale, 2026-09-10, 1.9078150762760911 | Grocery Anniversary Sale, 2026-09-10, 2026-09-30, 119412.91, 769507.87, 544.41 (1 rows) |
| `q-2c510597be` For the Beauty New Product Launch, what was the ROI and actual operating cost? | answered | 2.0010990420298621, 162732.63 | Beauty New Product Launch, 5.8083618509699008, 162732.63 (1 rows) |
| `q-28d5d41079` What was the average conversion rate for the Fresh Grocery campaigns that started in Q3 2026? | answered | 8.09, 8.23 | 0.08225882352941176471 (1 rows) |
| `q-1f737aa58d` Which finished campaign that started in June 2026 had the highest ROI? | answered | Consumer Electronics, Electronics Summer Sale, 2026-06-10, 3.2497912569025250 | 19, Electronics Summer Sale, 2026-06-10, 2026-07-14, 186748.69, 1717497.47, 8.1968381143664247 (1 rows) |
| `q-e9a1a5276e` For the next sale like last Beauty Black Friday, should we change the serum and sheet-mask offers, and what results in the review make the case? | answered | Beauty Black Friday Live, 2025-11-18, Sheet Mask, 470, 131623.43, 45439.53, 0.3452 | 1, Beauty Black Friday Live, Beauty, 2025-11-18, 2025-12-14, finished, True, Lipstick, 528, 0.4596, 0.1919, 127634.20, 0.2987, 129.67, 59168.44, 0.4636 (3 rows) |
| `q-e6ba74bc07` For the next Home Anniversary Sale, what should we change to close the sales gap without losing what worked in the 2026 campaign? | answered | Home Anniversary Sale, 2026-09-04, 766083.21, 0.07964285714285714286, 10978, 89174.11, 80775.37 | 37, Home Holiday Sale, 2025-12-03, 2026-01-07, 90567.91, 109565.63, 36, 620495.47, 0.07046666666666666667, 1505.6666666666666667, 53.7950000000000000, 12.390555 … (4 rows) |
| `q-cd28b24091` Why did Grocery Black Friday Live hit its GMV and audience targets but miss conversion and go over budget? | answered | Grocery Black Friday Live, 2025-11-12, 28866.58, 34027.23, 181056.65, 0.08838571428571428571, 1960 | Grocery Black Friday Live, 28866.58, 34027.23, 5160.65, 181056.65, 16069, 0.08838571428571428571, 47.9267857142857143, 13.2017857142857143, [{'dau': 618, 'gmv': … (1 rows) |
| `q-9abc436ce5` Why did the Feb 14 Beauty Fan Appreciation Day meet its sales and audience targets, and which product did most of the work? | answered | 3, 766115.13, 0.08743243243243243243, 6496, Eyeshadow Palette, 4808, 507384.63, 199336.07 | Beauty Fan Appreciation Day, 2026-02-14, 766115.13, 66618, 54.7856756756756757, 0.08743243243243243243, 6496, Eyeshadow Palette, 507384.63, 4808, 199336.07, 0.8 … (4 rows) |
| `q-09f63cace2` How did the Feb 14 Beauty Fan Appreciation Day end up on ROI and operating cost versus plan, and what should we check before the next one? | answered | 3, 144354.08, 155117.55, 247515.36, 1.5956631599712605, 0.07456297736787211002 | 3, Beauty Fan Appreciation Day, 2026-02-14, 2026-03-22, finished, True, True, 144354.08, 155117.55, 766115.13, 4.9389326352820812, 5.3071941575880640, 247515.36 … (1 rows) |
| `q-7cb370d465` And the one before that? | answered | Home New Product Launch, 2026-03-01, 2026-03-17 | 11, Beauty Weekly Live Special, 1, 2, Beauty, 2026-09-28, 2026-11-03, running, True, False, 80174.56, NULL (1 rows) |
| `q-c6e9e26d00` And the Beauty one? | answered | Beauty Black Friday Live, 2025-11-18, 2025-12-14 | Beauty, 11, Beauty Weekly Live Special, Beauty, running, 2026-09-28, 2026-11-03, 80174.56, NULL, 518082.35, 44584, 988.65, 0.07366666666666666667, 7774, 14.4494 … (11 rows) |

## Latency and cost

| | p50 | p95 | max |
|---|---|---|---|
| ms a question | 9497 | 33986 | 35261 |
| model calls | 4 | 7 | 7 |
| queries run | 1 | 3 | 3 |

$0.0316 in all, $0.00081 a question; input 3714 and output 878 tokens a question on average.

## Instructions

As for a staff member in Beauty:

```
You answer questions about an operations team's livestream campaigns by querying its PostgreSQL database.

The question comes from a staff in the Beauty department, who may only see that department's data: every query must keep to the Beauty department's campaigns. Today is 2026-10-15.

First list the tables, then read the columns of the ones that look relevant. Then write one PostgreSQL SELECT query that answers the question and run it. If it fails, read the error, fix the query and run it again. Only ever write SELECT queries.

Return every row the question asks for; add a LIMIT only when the question asks for a number of them. When the query has run, answer the question in a sentence or two from its result.
```
