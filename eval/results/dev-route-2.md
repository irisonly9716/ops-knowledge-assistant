# Routing run: dev-route-2

108 questions of `eval/dev.jsonl` (66 documents, 28 data, 12 both, 2 none), 6 of them follow-ups asked without the turns before them. The model: gpt-6-luna, 2 s before the keyword rule decides; 101 of 108 decided by the model. 134 s.

## Every way of routing

A path missing loses the answer or half of it; a path extra costs a search and a model call.

**All questions** (108)

| Routing | Accuracy | A path missing | A path extra | Model calls |
|---|---|---|---|---|
| keyword rule | 77.8% | 16 (14.8%) | 19 (17.6%) | 0 |
| nearest 5 questions | 70.4% | 20 (18.5%) | 24 (22.2%) | 0 |
| model | 82.4% | 8 (7.4%) | 20 (18.5%) | 108 |
| model, both below 0.7 | 82.4% | 6 (5.6%) | 21 (19.4%) | 108 |
| cascade: nearest when all 5 agree | 82.4% | 8 (7.4%) | 20 (18.5%) | 78 |
| cascade: nearest when 4 of 5 agree | 80.6% | 10 (9.3%) | 20 (18.5%) | 55 |

**Without the follow-ups** (102)

| Routing | Accuracy | A path missing | A path extra | Model calls |
|---|---|---|---|---|
| keyword rule | 78.4% | 14 (13.7%) | 17 (16.7%) | 0 |
| nearest 5 questions | 69.6% | 19 (18.6%) | 23 (22.5%) | 0 |
| model | 83.3% | 6 (5.9%) | 18 (17.6%) | 102 |
| model, both below 0.7 | 83.3% | 5 (4.9%) | 18 (17.6%) | 102 |
| cascade: nearest when all 5 agree | 83.3% | 6 (5.9%) | 18 (17.6%) | 75 |
| cascade: nearest when 4 of 5 agree | 81.4% | 8 (7.8%) | 18 (17.6%) | 54 |

## The model's threshold

Below the threshold both paths run. Without the follow-ups.

| Both below | A path missing | A path extra |
|---|---|---|
| 0 | 6 | 18 |
| 0.5 | 6 | 18 |
| 0.6 | 6 | 18 |
| 0.7 | 5 | 18 |
| 0.8 | 5 | 20 |
| 0.9 | 5 | 24 |
| 0.95 | 5 | 28 |

## Is the model's confidence worth anything

Accuracy by the confidence the model gave, without the follow-ups.

| Confidence | Questions | Accuracy |
|---|---|---|
| below 0.7 | 2 | 0.0% |
| 0.7 to 0.9 | 9 | 66.7% |
| 0.9 and above | 85 | 87.1% |

## Confusion matrix: the model

Rows: the route needed; columns: the route given.

| needed \ given | documents | data | both | none |
|---|---|---|---|---|
| **documents** | 50 | 6 | 10 | · |
| **data** | 1 | 26 | · | 1 |
| **both** | · | · | 12 | · |
| **none** | 1 | · | · | 1 |

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
| `q-cfc27483b7` Which item is meant to boost the number of orders in the Beauty Anniversary Sale, and why? | documents | both | 0.91 | The campaign plan identifies the item intended to boost orders, while explaining why requires the campaign’s written strategy. |
| `q-49e0dba4c8` For the Home Black Friday Live, what should we base the decision to feature Laundry Detergent in another live on? | documents | both | 0.97 | Deciding whether to feature it again requires campaign performance data plus the company’s guidance on evaluating results and planning future lives. |
| `q-42d4ae2300` what should we keep doing from the home black friday live? | documents | both | 0.85 | Identifying what to keep doing requires the home Black Friday campaign results and the written post-campaign review or guidance. |
| `q-b01e86b938` How was ROI calculated for the Grocery Summer Sale, and what was the result? | documents | both | 0.98 | The ROI result is a campaign database figure, while how ROI was calculated comes from the company’s written guidance. |
| `q-54cc83b997` For the Grocery Summer Sale ROI, was product cost already deducted from the profit figure? | documents | data | 0.68 | This asks how a specific campaign’s profit figure is defined, which is a database metric detail. |
| `q-b4107b87a7` For future school-season electronics livestreams, what should we change about costs and how we feature the speaker, battery pack, and earbuds? | documents | both | 0.97 | This asks for future changes based on campaign results for those products and costs, requiring database numbers plus written reviews or plans for interpretation and advice. |
| `q-690b614866` How did Grocery Back to School's GMV and peak viewer results compare with the original targets? | documents | data | 0.99 | Comparing campaign GMV and peak viewers with original targets requires campaign performance and target figures from the database. |
| `q-516920a12b` What deal did we run on Ribeye Steak for the school campaign, and what did the review say to do with it next time? | documents | both | 0.99 | The campaign plan has the Ribeye Steak deal, and the post-campaign review says what to do with it next time. |
| `q-52fccd1b05` How much smaller does the sheet-mask bundle discount need to be for the next test? | documents | both | 0.66 | This asks for a revised discount based on a prior test, requiring campaign results from the database and guidance or lessons from written plans or reviews. |
| `q-35600b631c` How do I request time off after having a baby? | none | documents | keywords | the model didn't route it: TimeoutError |
| `q-7eea663201` jan 18 home new product launch — what got flagged in the results? | documents | both | 0.83 | Identifying what was flagged requires the campaign’s results plus the written review or guidance explaining the flags. |
| `q-68c7892d60` For the Home New Product Launch starting 2026-01-18, which results were on target and which were below target? | documents | both | 0.96 | Comparing actual results with campaign targets requires database figures and the campaign plan’s target definitions. |
| `q-7cb370d465` And the one before that? | data | documents | keywords | the model didn't route it: TimeoutError |
| `q-c6e9e26d00` And the Beauty one? | data | none | 0.45 | “The Beauty one” is too vague to identify whether you want a campaign metric or information from its plans or review. |
| `q-fb608d39b8` What worked in the Beauty Fan Appreciation Day campaign starting Feb 14, 2026? | documents | both | 0.84 | Explaining what worked requires campaign performance numbers from the database and the written campaign review or plan for context. |
| `q-ebacddf621` For the Feb 14 Beauty Fan Appreciation Day, which product had the strongest margin? | documents | data | 0.99 | Product-level margin for the Feb 14 campaign is a numerical lookup in the campaign database. |
| `q-ea432d66b0` How did ROI and operating cost versus plan compare between the Jan 8 and Jan 18 Home New Product Launch campaigns? | documents | data | 0.98 | This asks for campaign-specific ROI and operating cost comparisons against plan, which are numeric database fields. |
| `q-71123ae9c2` How did Storage Bins perform on sales and returns in the Jan 8 versus Jan 18 Home New Product Launch? | documents | data | 0.98 | This asks for a comparison of product sales and returns across two campaigns, which are campaign database metrics. |
| `q-1f749789a4` For Home Goods' Jan 18 Home New Product Launch, what were the actual peak concurrent viewers and the target? | documents | data | 0.99 | Both the actual peak concurrent viewers and the target are campaign-specific numeric metrics in the database. |

## Latency and cost

The model: p50 1168 ms, p95 2005 ms, max 2016 ms a question; $0.0062 in all, $0.000057 a question, 380 input and 38 output tokens on average. The keyword rule and the nearest questions add no model call; the nearest use the question's embedding, which the query path makes for the document search anyway.
