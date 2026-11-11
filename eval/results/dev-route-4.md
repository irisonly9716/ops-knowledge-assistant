# Routing run: dev-route-4

108 questions of `eval/dev.jsonl` (66 documents, 28 data, 12 both, 2 none), 6 of them follow-ups asked without the turns before them. The model: gpt-6-luna, 2 s before the keyword rule decides; 107 of 108 decided by the model. 131 s.

## Every way of routing

A path missing loses the answer or half of it; a path extra costs a search and a model call.

**All questions** (108)

| Routing | Accuracy | A path missing | A path extra | Model calls |
|---|---|---|---|---|
| keyword rule | 77.8% | 16 (14.8%) | 19 (17.6%) | 0 |
| nearest 5 questions | 70.4% | 20 (18.5%) | 24 (22.2%) | 0 |
| model | 82.4% | 5 (4.6%) | 20 (18.5%) | 108 |
| model, both below 0.8 | 82.4% | 5 (4.6%) | 26 (24.1%) | 108 |
| cascade: nearest when all 5 agree | 81.5% | 5 (4.6%) | 20 (18.5%) | 78 |
| cascade: nearest when 4 of 5 agree | 80.6% | 8 (7.4%) | 19 (17.6%) | 55 |

**Without the follow-ups** (102)

| Routing | Accuracy | A path missing | A path extra | Model calls |
|---|---|---|---|---|
| keyword rule | 78.4% | 14 (13.7%) | 17 (16.7%) | 0 |
| nearest 5 questions | 69.6% | 19 (18.6%) | 23 (22.5%) | 0 |
| model | 83.3% | 3 (2.9%) | 18 (17.6%) | 102 |
| model, both below 0.8 | 83.3% | 3 (2.9%) | 22 (21.6%) | 102 |
| cascade: nearest when all 5 agree | 82.4% | 3 (2.9%) | 18 (17.6%) | 75 |
| cascade: nearest when 4 of 5 agree | 81.4% | 6 (5.9%) | 17 (16.7%) | 54 |

## The model's threshold

Below the threshold both paths run. Without the follow-ups.

| Both below | A path missing | A path extra |
|---|---|---|
| 0 | 3 | 18 |
| 0.5 | 3 | 18 |
| 0.6 | 3 | 18 |
| 0.7 | 3 | 18 |
| 0.8 | 3 | 22 |
| 0.9 | 2 | 25 |
| 0.95 | 2 | 34 |

## Is the model's confidence worth anything

Accuracy by the confidence the model gave, without the follow-ups.

| Confidence | Questions | Accuracy |
|---|---|---|
| 0.7 to 0.9 | 12 | 58.3% |
| 0.9 and above | 89 | 87.6% |

## Confusion matrix: the model

Rows: the route needed; columns: the route given.

| needed \ given | documents | data | both | none |
|---|---|---|---|---|
| **documents** | 50 | 3 | 12 | 1 |
| **data** | · | 25 | 1 | 2 |
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
| `q-b2b23c4951` What does “cost price” mean? | documents | none | 0.98 | The sources describe campaign operations and metrics, but do not define this general business term. |
| `q-49e0dba4c8` For the Home Black Friday Live, what should we base the decision to feature Laundry Detergent in another live on? | documents | both | 0.87 | Deciding whether to feature it again requires the campaign’s product results from the database and the written review or guidance explaining how to use those results. |
| `q-42d4ae2300` what should we keep doing from the home black friday live? | documents | both | 0.91 | Identifying what to keep doing requires the campaign’s results from the database and lessons or recommendations from its post-campaign review. |
| `q-b01e86b938` How was ROI calculated for the Grocery Summer Sale, and what was the result? | documents | both | 0.97 | The calculation method is documented, while the campaign’s ROI result is in the database. |
| `q-54cc83b997` For the Grocery Summer Sale ROI, was product cost already deducted from the profit figure? | documents | data | 0.86 | This asks how the campaign database’s profit figure is calculated, rather than for written operating guidance. |
| `q-b4107b87a7` For future school-season electronics livestreams, what should we change about costs and how we feature the speaker, battery pack, and earbuds? | documents | both | 0.97 | This asks for recommendations based on past campaign results, requiring database cost and product performance numbers plus written reviews or plans for guidance. |
| `q-690b614866` How did Grocery Back to School's GMV and peak viewer results compare with the original targets? | documents | both | 0.99 | The results are in the campaign database, while the original GMV and peak viewer targets are in the campaign plan. |
| `q-516920a12b` What deal did we run on Ribeye Steak for the school campaign, and what did the review say to do with it next time? | documents | both | 0.99 | The deal is in the campaign records or plan, while the recommendation for next time is in the post-campaign review. |
| `q-52fccd1b05` How much smaller does the sheet-mask bundle discount need to be for the next test? | documents | both | 0.87 | The discount used and its results are in the campaign data, while the recommended adjustment for the next test is in the post-campaign review or plan. |
| `q-035cfdfd0c` Which Beauty campaign had the highest GMV, and who hosted it? | data | both | keywords | the model didn't route it: TimeoutError |
| `q-7eea663201` jan 18 home new product launch — what got flagged in the results? | documents | both | 0.73 | Identifying what was flagged requires the Jan 18 campaign results from the database and the written review or flag criteria to interpret them. |
| `q-68c7892d60` For the Home New Product Launch starting 2026-01-18, which results were on target and which were below target? | documents | both | 0.99 | Comparing campaign results with targets requires database results and the targets in the written campaign plan. |
| `q-7cb370d465` And the one before that? | data | none | 0.97 | There is no prior campaign or reference established in the conversation to identify what “the one before that” means. |
| `q-c6e9e26d00` And the Beauty one? | data | none | 0.98 | “The Beauty one” lacks context and does not specify a question about the company’s documents or campaign database. |
| `q-fb608d39b8` What worked in the Beauty Fan Appreciation Day campaign starting Feb 14, 2026? | documents | both | 0.84 | Assessing what worked requires campaign results from the database and the campaign review or plan for context. |
| `q-ebacddf621` For the Feb 14 Beauty Fan Appreciation Day, which product had the strongest margin? | documents | data | 0.98 | The database contains product-level sales and profit figures needed to compare margins for that campaign. |
| `q-ea432d66b0` How did ROI and operating cost versus plan compare between the Jan 8 and Jan 18 Home New Product Launch campaigns? | documents | both | 0.99 | ROI and operating costs are in the campaign database, while planned operating costs are in the campaign plans for comparison. |
| `q-71123ae9c2` How did Storage Bins perform on sales and returns in the Jan 8 versus Jan 18 Home New Product Launch? | documents | data | 0.98 | Sales and returns by product and campaign are numerical metrics held in the campaign database. |
| `q-1f749789a4` For Home Goods' Jan 18 Home New Product Launch, what were the actual peak concurrent viewers and the target? | documents | both | 0.99 | The actual peak concurrent viewers are in the campaign database, while the target is only in the campaign plan. |

## Latency and cost

The model: p50 1152 ms, p95 1705 ms, max 2006 ms a question; $0.0070 in all, $0.000065 a question, 442 input and 41 output tokens on average. The keyword rule and the nearest questions add no model call; the nearest use the question's embedding, which the query path makes for the document search anyway.
