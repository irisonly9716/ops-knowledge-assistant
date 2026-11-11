# Routing run: dev-route-1

108 questions of `eval/dev.jsonl` (66 documents, 28 data, 12 both, 2 none), 6 of them follow-ups asked without the turns before them. The model: gpt-6-luna, 2 s before the keyword rule decides; 107 of 108 decided by the model. 134 s.

## Every way of routing

A path missing loses the answer or half of it; a path extra costs a search and a model call.

**All questions** (108)

| Routing | Accuracy | A path missing | A path extra | Model calls |
|---|---|---|---|---|
| keyword rule | 77.8% | 16 (14.8%) | 19 (17.6%) | 0 |
| nearest 5 questions | 70.4% | 20 (18.5%) | 24 (22.2%) | 0 |
| model | 83.3% | 7 (6.5%) | 18 (16.7%) | 108 |
| model, both below 0.7 | 83.3% | 7 (6.5%) | 21 (19.4%) | 108 |
| cascade: nearest when all 5 agree | 83.3% | 7 (6.5%) | 18 (16.7%) | 78 |
| cascade: nearest when 4 of 5 agree | 80.6% | 10 (9.3%) | 19 (17.6%) | 55 |

**Without the follow-ups** (102)

| Routing | Accuracy | A path missing | A path extra | Model calls |
|---|---|---|---|---|
| keyword rule | 78.4% | 14 (13.7%) | 17 (16.7%) | 0 |
| nearest 5 questions | 69.6% | 19 (18.6%) | 23 (22.5%) | 0 |
| model | 82.4% | 7 (6.9%) | 18 (17.6%) | 102 |
| model, both below 0.7 | 82.4% | 7 (6.9%) | 18 (17.6%) | 102 |
| cascade: nearest when all 5 agree | 82.4% | 7 (6.9%) | 18 (17.6%) | 75 |
| cascade: nearest when 4 of 5 agree | 80.4% | 9 (8.8%) | 18 (17.6%) | 54 |

## The model's threshold

Below the threshold both paths run. Without the follow-ups.

| Both below | A path missing | A path extra |
|---|---|---|
| 0 | 7 | 18 |
| 0.5 | 7 | 18 |
| 0.6 | 7 | 18 |
| 0.7 | 7 | 18 |
| 0.8 | 6 | 18 |
| 0.9 | 6 | 23 |
| 0.95 | 5 | 31 |

## Is the model's confidence worth anything

Accuracy by the confidence the model gave, without the follow-ups.

| Confidence | Questions | Accuracy |
|---|---|---|
| 0.7 to 0.9 | 10 | 50.0% |
| 0.9 and above | 91 | 86.8% |

## Confusion matrix: the model

Rows: the route needed; columns: the route given.

| needed \ given | documents | data | both | none |
|---|---|---|---|---|
| **documents** | 50 | 7 | 9 | · |
| **data** | · | 28 | · | · |
| **both** | · | · | 12 | · |
| **none** | 1 | 1 | · | · |

## Confusion matrix: the keyword rule

Rows: the route needed; columns: the route given.

| needed \ given | documents | data | both | none |
|---|---|---|---|---|
| **documents** | 55 | 8 | 3 | · |
| **data** | 3 | 22 | 3 | · |
| **both** | 3 | 2 | 7 | · |
| **none** | 1 | 1 | · | · |

## Confusion matrix: the nearest 5 questions

Rows: the route needed; columns: the route given.

| needed \ given | documents | data | both | none |
|---|---|---|---|---|
| **documents** | 52 | 6 | 8 | · |
| **data** | 6 | 20 | 2 | · |
| **both** | 6 | 2 | 4 | · |
| **none** | 2 | · | · | · |

## Where the model went wrong

| Question | Needed | Given | Confidence | Its reason |
|---|---|---|---|---|
| `q-504f369d69` What does gross margin mean in retail? | none | data | keywords | the model didn't route it: TimeoutError |
| `q-cfc27483b7` Which item is meant to boost the number of orders in the Beauty Anniversary Sale, and why? | documents | both | 0.94 | Identifying the item requires the campaign plan, and explaining why it is intended to boost orders may require the plan’s rationale. |
| `q-49e0dba4c8` For the Home Black Friday Live, what should we base the decision to feature Laundry Detergent in another live on? | documents | both | 0.94 | Deciding whether to feature it again needs the Home Black Friday Live’s performance data and campaign-plan or review guidance on how to evaluate and adjust the lineup. |
| `q-42d4ae2300` what should we keep doing from the home black friday live? | documents | both | 0.85 | Identifying what to keep doing requires the home Black Friday campaign results and the written review or guidance on what worked and why. |
| `q-b01e86b938` How was ROI calculated for the Grocery Summer Sale, and what was the result? | documents | both | 0.97 | The calculation method comes from campaign documentation, while the campaign’s ROI result comes from the database. |
| `q-54cc83b997` For the Grocery Summer Sale ROI, was product cost already deducted from the profit figure? | documents | data | 0.79 | This asks how profit is calculated for a specific campaign, which requires consulting its campaign data. |
| `q-b4107b87a7` For future school-season electronics livestreams, what should we change about costs and how we feature the speaker, battery pack, and earbuds? | documents | both | 0.97 | This asks for future recommendations based on campaign cost results and product performance, combining database numbers with written plans or reviews. |
| `q-690b614866` How did Grocery Back to School's GMV and peak viewer results compare with the original targets? | documents | data | 0.98 | This asks for campaign results compared with target numbers, which are in the campaign database. |
| `q-516920a12b` What deal did we run on Ribeye Steak for the school campaign, and what did the review say to do with it next time? | documents | both | 0.99 | The campaign deal is in the campaign database, while the next-time recommendation is in the written post-campaign review. |
| `q-52fccd1b05` How much smaller does the sheet-mask bundle discount need to be for the next test? | documents | both | 0.78 | This requires the sheet-mask bundle’s campaign results from the database and written guidance or review on how to adjust the next test. |
| `q-35600b631c` How do I request time off after having a baby? | none | documents | 0.98 | This is a company leave procedure question, which would be covered by written policies or guidelines. |
| `q-7eea663201` jan 18 home new product launch — what got flagged in the results? | documents | both | 0.82 | Identifying what was flagged requires the launch’s campaign results and the written review explaining the flagged issues. |
| `q-68c7892d60` For the Home New Product Launch starting 2026-01-18, which results were on target and which were below target? | documents | data | 0.92 | This asks which campaign results met or missed targets, which requires comparing campaign metrics against targets in the database. |
| `q-fb608d39b8` What worked in the Beauty Fan Appreciation Day campaign starting Feb 14, 2026? | documents | both | 0.82 | Assessing what worked requires campaign results from the database and the campaign plan or review to explain the effective tactics. |
| `q-ebacddf621` For the Feb 14 Beauty Fan Appreciation Day, which product had the strongest margin? | documents | data | 0.98 | The strongest margin requires comparing product-level financial figures in the campaign database. |
| `q-ea432d66b0` How did ROI and operating cost versus plan compare between the Jan 8 and Jan 18 Home New Product Launch campaigns? | documents | data | 0.98 | This asks for a numerical comparison of ROI and operating costs against plan across two campaigns, which is in the campaign database. |
| `q-71123ae9c2` How did Storage Bins perform on sales and returns in the Jan 8 versus Jan 18 Home New Product Launch? | documents | data | 0.98 | This asks for sales and returns figures for a product across two campaigns, which are in the campaign database. |
| `q-1f749789a4` For Home Goods' Jan 18 Home New Product Launch, what were the actual peak concurrent viewers and the target? | documents | data | 0.99 | Actual peak concurrent viewers and the target are campaign-specific numeric metrics in the database. |

## Latency and cost

The model: p50 1199 ms, p95 1664 ms, max 2000 ms a question; $0.0065 in all, $0.000061 a question, 403 input and 41 output tokens on average. The keyword rule and the nearest questions add no model call; the nearest use the question's embedding, which the query path makes for the document search anyway.
