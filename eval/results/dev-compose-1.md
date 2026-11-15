# Composing evaluation run: dev-compose-1

27 questions whose answer `dev-flow-3` wrote from both, their inputs taken from one run through the query path; 1 left out because this run didn't write them from both: `q-e9a1a5276e` (written by documents this time). Each composed again at none, low, 2 times, graded by gpt-6-sol.

| Effort | Answers | Answered / refused / invalid | Coverage, refusals as 0 | By repeat | Faithfulness | By repeat | Composing p50 / p95 | Output tokens |
|---|---|---|---|---|---|---|---|---|
| none | 54 | 54 / 0 / 0 | 62.5% | 62.8% / 62.2% | 96.0% | 96.0% / 96.1% | 1768 / 2793 ms | 105 |
| low | 54 | 54 / 0 / 0 | 66.0% | 67.0% / 65.1% | 96.9% | 95.4% / 98.4% | 1622 / 3980 ms | 122 |

**The rule: low does not replace none.**

- not met: coverage +3.5 points, at least 5 needed
- met: none differs from itself by 0.6 points between repeats
- met: faithfulness +0.8 points, not lower needed
- met: the composing call's p50 -146 ms, at most +1000 ms allowed

Cost: composing $0.0223, grading $0.9609.

## Per question

| Question | none | low |
|---|---|---|
| `q-524c9ea0a1` what's the hero item for the beauty anniversary sale? | 50.0% / 50.0% | 50.0% / 50.0% |
| `q-49e0dba4c8` For the Home Black Friday Live, what should we base the decision to feature Laundry Detergent in another live on? | 100.0% / 100.0% | 100.0% / 100.0% |
| `q-b01e86b938` How was ROI calculated for the Grocery Summer Sale, and what was the result? | 100.0% / 100.0% | 100.0% / 100.0% |
| `q-54cc83b997` For the Grocery Summer Sale ROI, was product cost already deducted from the profit figure? | 50.0% / 50.0% | 50.0% / 50.0% |
| `q-b4107b87a7` For future school-season electronics livestreams, what should we change about costs and how we feature the speaker, battery pack, and earbuds? | 66.7% / 66.7% | 100.0% / 100.0% |
| `q-690b614866` How did Grocery Back to School's GMV and peak viewer results compare with the original targets? | 100.0% / 100.0% | 100.0% / 100.0% |
| `q-516920a12b` What deal did we run on Ribeye Steak for the school campaign, and what did the review say to do with it next time? | 50.0% / 50.0% | 50.0% / 50.0% |
| `q-bde7bedf09` By when do we need to finish reviewing the return rates for all three Grocery Back to School products? | – / – | – / – |
| `q-123c6ee65c` What was the conversion rate for our most recent product launch? | 0.0% / 0.0% | 0.0% / 0.0% |
| `q-f6c62704bd` For our next Beauty Black Friday live, how should we set the operating-cost budget, and which results from the 2025 review support that change? | 66.7% / 66.7% | 66.7% / 66.7% |
| `q-e6ba74bc07` For the next Home Anniversary Sale, what should we change to close the sales gap without losing what worked in the 2026 campaign? | 66.7% / 66.7% | 66.7% / 66.7% |
| `q-5a69071550` When we plan another anniversary sale, how should we rethink which products get airtime given the sales and returns from the last one? | 25.0% / 50.0% | 50.0% / 50.0% |
| `q-a5902b702d` For the next Electronics Summer Sale, what should we change about traffic and spending plans, based on the 2026 post-campaign review and the campaign results? | 25.0% / 25.0% | 50.0% / 25.0% |
| `q-b482d79dd3` When we build the next summer electronics lineup, which products should get a bigger role or a closer look, and what results in the 2026 Summer Sale review support that? | 50.0% / 0.0% | 75.0% / 50.0% |
| `q-cd28b24091` Why did Grocery Black Friday Live hit its GMV and audience targets but miss conversion and go over budget? | 66.7% / 66.7% | 66.7% / 66.7% |
| `q-40cb4f5cea` How did the product mix and returns affect the Black Friday grocery livestream’s results against its targets? | 25.0% / 50.0% | 25.0% / 50.0% |
| `q-9abc436ce5` Why did the Feb 14 Beauty Fan Appreciation Day meet its sales and audience targets, and which product did most of the work? | 0.0% / 33.3% | 33.3% / 0.0% |
| `q-09f63cace2` How did the Feb 14 Beauty Fan Appreciation Day end up on ROI and operating cost versus plan, and what should we check before the next one? | 66.7% / 66.7% | 66.7% / 66.7% |
| `q-b57ed61f60` Why did the Jan 18 Home New Product Launch meet its sales and conversion targets but miss the viewer and cost targets? | 75.0% / 25.0% | 75.0% / 50.0% |
| `q-0f544827a5` For the Jan 18 launch, how did the product mix help sales land on target, and what product issues still need attention? | 50.0% / 50.0% | 50.0% / 50.0% |
| `q-a5d100d152` For the November 2025 Grocery Fan Appreciation Day, what could make the product selection change during the event? | 100.0% / 100.0% | 100.0% / 100.0% |
| `q-7eea663201` jan 18 home new product launch — what got flagged in the results? | 100.0% / 100.0% | 100.0% / 100.0% |
| `q-68c7892d60` For the Home New Product Launch starting 2026-01-18, which results were on target and which were below target? | 100.0% / 100.0% | 100.0% / 100.0% |
| `q-940ab5829f` for the feb 14 beauty fan appreciation day, what do we need to double-check before go-live? | 100.0% / 100.0% | 100.0% / 100.0% |
| `q-aaf1a5a70a` For the Feb 14 Beauty Fan Appreciation Day, how should we explain the palette deal alongside the purchase-based savings offer? | 100.0% / 100.0% | 100.0% / 100.0% |
| `q-fb608d39b8` What worked in the Beauty Fan Appreciation Day campaign starting Feb 14, 2026? | 0.0% / 0.0% | 0.0% / 0.0% |
| `q-ea432d66b0` How did ROI and operating cost versus plan compare between the Jan 8 and Jan 18 Home New Product Launch campaigns? | 100.0% / 100.0% | 66.7% / 100.0% |
