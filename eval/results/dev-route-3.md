# Routing run: dev-route-3

108 questions of `eval/dev.jsonl` (66 documents, 28 data, 12 both, 2 none), 6 of them follow-ups asked without the turns before them. The model: gpt-6-luna, 2 s before the keyword rule decides; 107 of 108 decided by the model. 128 s.

## Every way of routing

A path missing loses the answer or half of it; a path extra costs a search and a model call.

**All questions** (108)

| Routing | Accuracy | A path missing | A path extra | Model calls |
|---|---|---|---|---|
| keyword rule | 77.8% | 16 (14.8%) | 19 (17.6%) | 0 |
| nearest 5 questions | 70.4% | 20 (18.5%) | 24 (22.2%) | 0 |
| model | 84.3% | 4 (3.7%) | 19 (17.6%) | 108 |
| model, both below 0.8 | 84.3% | 4 (3.7%) | 24 (22.2%) | 108 |
| cascade: nearest when all 5 agree | 83.3% | 4 (3.7%) | 19 (17.6%) | 78 |
| cascade: nearest when 4 of 5 agree | 81.5% | 7 (6.5%) | 19 (17.6%) | 55 |

**Without the follow-ups** (102)

| Routing | Accuracy | A path missing | A path extra | Model calls |
|---|---|---|---|---|
| keyword rule | 78.4% | 14 (13.7%) | 17 (16.7%) | 0 |
| nearest 5 questions | 69.6% | 19 (18.6%) | 23 (22.5%) | 0 |
| model | 84.3% | 3 (2.9%) | 18 (17.6%) | 102 |
| model, both below 0.8 | 84.3% | 3 (2.9%) | 21 (20.6%) | 102 |
| cascade: nearest when all 5 agree | 83.3% | 3 (2.9%) | 18 (17.6%) | 75 |
| cascade: nearest when 4 of 5 agree | 81.4% | 6 (5.9%) | 18 (17.6%) | 54 |

## The model's threshold

Below the threshold both paths run. Without the follow-ups.

| Both below | A path missing | A path extra |
|---|---|---|
| 0 | 3 | 18 |
| 0.5 | 3 | 18 |
| 0.6 | 3 | 19 |
| 0.7 | 3 | 19 |
| 0.8 | 3 | 21 |
| 0.9 | 3 | 25 |
| 0.95 | 2 | 33 |

## Is the model's confidence worth anything

Accuracy by the confidence the model gave, without the follow-ups.

| Confidence | Questions | Accuracy |
|---|---|---|
| below 0.7 | 1 | 100.0% |
| 0.7 to 0.9 | 10 | 60.0% |
| 0.9 and above | 90 | 86.7% |

## Confusion matrix: the model

Rows: the route needed; columns: the route given.

| needed \ given | documents | data | both | none |
|---|---|---|---|---|
| **documents** | 50 | 3 | 13 | · |
| **data** | · | 27 | · | 1 |
| **both** | · | · | 12 | · |
| **none** | · | · | · | 2 |

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
| `q-cfc27483b7` Which item is meant to boost the number of orders in the Beauty Anniversary Sale, and why? | documents | both | 0.84 | Identifying the item and why it was chosen requires the campaign plan’s product strategy, and may need sales or order data to establish its intended effect. |
| `q-49e0dba4c8` For the Home Black Friday Live, what should we base the decision to feature Laundry Detergent in another live on? | documents | both | 0.87 | Deciding whether to feature it again needs the Home Black Friday Live’s product results from the database and the plan or review guidance on how to assess and adjust the lineup. |
| `q-42d4ae2300` what should we keep doing from the home black friday live? | documents | both | 0.94 | Identifying what to keep doing requires the campaign’s results from the database and lessons or recommendations from its post-campaign review. |
| `q-b01e86b938` How was ROI calculated for the Grocery Summer Sale, and what was the result? | documents | both | 0.98 | The calculation method is in the written guidance, while the Grocery Summer Sale’s ROI result is in the campaign database. |
| `q-54cc83b997` For the Grocery Summer Sale ROI, was product cost already deducted from the profit figure? | documents | data | 0.93 | Whether product cost is included in the campaign’s profit figure requires the Grocery Summer Sale database figures or definitions. |
| `q-b4107b87a7` For future school-season electronics livestreams, what should we change about costs and how we feature the speaker, battery pack, and earbuds? | documents | both | 0.97 | This asks what to change for future campaigns based on past results, requiring campaign cost and product-performance data plus written post-campaign guidance or plans. |
| `q-690b614866` How did Grocery Back to School's GMV and peak viewer results compare with the original targets? | documents | both | 0.99 | The results are in the campaign database, while the original targets are in the campaign plan. |
| `q-516920a12b` What deal did we run on Ribeye Steak for the school campaign, and what did the review say to do with it next time? | documents | both | 0.99 | The deal is in the campaign plan and the recommendation for next time is in the post-campaign review. |
| `q-52fccd1b05` How much smaller does the sheet-mask bundle discount need to be for the next test? | documents | both | 0.9 | This asks for an adjustment based on prior campaign results, which requires the database outcomes and the written post-campaign analysis or recommendations. |
| `q-7eea663201` jan 18 home new product launch — what got flagged in the results? | documents | both | 0.76 | Identifying what was flagged requires the Jan 18 launch results from the database and the written review or criteria explaining the flags. |
| `q-68c7892d60` For the Home New Product Launch starting 2026-01-18, which results were on target and which were below target? | documents | both | 0.99 | This requires comparing campaign results in the database with the targets in the campaign plan. |
| `q-c6e9e26d00` And the Beauty one? | data | none | 0.97 | “The Beauty one” does not specify what information is being requested. |
| `q-fb608d39b8` What worked in the Beauty Fan Appreciation Day campaign starting Feb 14, 2026? | documents | both | 0.87 | Explaining what worked requires campaign results from the database and the campaign review or plan for interpretation. |
| `q-ebacddf621` For the Feb 14 Beauty Fan Appreciation Day, which product had the strongest margin? | documents | data | 0.99 | Product-level profit and sales figures needed to compare margins are in the campaign database. |
| `q-ea432d66b0` How did ROI and operating cost versus plan compare between the Jan 8 and Jan 18 Home New Product Launch campaigns? | documents | both | 0.98 | The campaign database has ROI and operating cost results, while the campaign plans contain the planned figures needed for comparison. |
| `q-71123ae9c2` How did Storage Bins perform on sales and returns in the Jan 8 versus Jan 18 Home New Product Launch? | documents | data | 0.98 | Sales and returns for Storage Bins on those two campaign dates are numerical campaign database metrics. |
| `q-1f749789a4` For Home Goods' Jan 18 Home New Product Launch, what were the actual peak concurrent viewers and the target? | documents | both | 0.98 | The actual peak concurrent viewers are in the campaign database, while the target is only in the written campaign plan. |

## Latency and cost

The model: p50 1133 ms, p95 1562 ms, max 1988 ms a question; $0.0070 in all, $0.000065 a question, 442 input and 41 output tokens on average. The keyword rule and the nearest questions add no model call; the nearest use the question's embedding, which the query path makes for the document search anyway.
