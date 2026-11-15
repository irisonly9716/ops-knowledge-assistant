# Rewrite evaluation run: dev-rewrite-all-4

108 questions from `eval/dev.jsonl`, the rewrite alone, with each follow-up's turns. $0.0155; p50 848 ms, p95 1681 ms.

| Group | Questions | Rewritten | With a campaign | With a period | Left as asked, by why |
|---|---|---|---|---|---|
| follow-up | 6 | 5 | 1 | 0 | stands on its own 1 |
| relative word | 10 | 8 | 6 | 2 | stands on its own 2 |
| other | 92 | 3 | 2 | 1 | stands on its own 82; the model didn't rewrite it: TimeoutError 3; the model wrote the question as it was 3; the reply was refused: references without a question 1 |

## follow-up

| Question | Rewritten | Reference |
|---|---|---|
| `q-18906f3f3f` user: Who owns the Beauty sale event inventory check? / assistant: The Beauty Supply Chain team owns the check. / **Who signs off on it?** | Who signs off on the Beauty sale event inventory check? | – |
| `q-cde842f99a` user: Who confirms stock counts for the Beauty sale event inventory check? / assistant: Warehouse & Logistics confirms stock counts. / **who updates the stream plan if a shade gets pulled?** | – (stands on its own) | – |
| `q-40d099213e` user: Which core numbers do we review after an Electronics livestream? / assistant: Conversion rate, demo-to-purchase rate, and returns marked “not as de / **And what do we check in the product demonstrations?** | What do we check in the product demonstrations during an Electronics livestream? | – |
| `q-8899a36ebc` user: What do we do if a wrong product spec was shown on screen? / assistant: Correct it at the start of the next stream and confirm the corrected  / **and where do we log the findings?** | Where do we log the findings about a wrong product specification shown on screen? | – |
| `q-7cb370d465` user: When was the most recent finished Home Goods New Product Launch? / assistant: Home New Product Launch ran from 2026-03-13 to 2026-04-01. / **And the one before that?** | When was the {campaign}? | campaign: New Product Launch, Home Goods, 2 |
| `q-c6e9e26d00` user: When did the Electronics Black Friday Live campaign run? / assistant: Electronics Black Friday Live ran from 2025-11-16 to 2025-12-12. / **And the Beauty one?** | When did the Beauty Black Friday Live campaign run? | – |

## relative word

| Question | Rewritten | Reference |
|---|---|---|
| `q-2b3264c04e` What does the Sale Event Inventory Check require if the previous batch’s defect rate was above 2%? | – (stands on its own) | – |
| `q-22b362d5b5` In the Consumer Electronics sale-event inventory check, what happens if the previous batch's defect rate was above 2%? | – (stands on its own) | – |
| `q-cd428e41fe` How much GMV did last Black Friday's electronics livestream bring in? | How much GMV did {campaign}'s electronics livestream bring in? | campaign: Black Friday, electronics, 1 |
| `q-123c6ee65c` What was the conversion rate for our most recent product launch? | What was the conversion rate for {campaign}? | campaign: product launch, None, 1 |
| `q-617dabba49` How much GMV did the latest fan appreciation campaign generate? | How much GMV did {campaign} generate? | campaign: fan appreciation campaign, None, 1 |
| `q-b66ecb8a0e` What was the average conversion rate for last Black Friday's grocery live? | What was the average conversion rate for {campaign}'s grocery live? | campaign: Black Friday, grocery, 1 |
| `q-fa3c627a7f` Which of our grocery campaigns this year had the highest peak live audience, and who hosted it? | Which of our grocery campaigns in {period} had the highest peak live audience, and who hosted it? | period: this_year |
| `q-1c63e12ded` What was the highest peak concurrent viewer count among campaigns that started in the last three full months? | What was the highest peak concurrent viewer count among campaigns that started in {period}? | period: last_full_months 3 |
| `q-e9a1a5276e` For the next sale like last Beauty Black Friday, should we change the serum and sheet-mask offers, and what results in the review make the case? | For the next sale like {campaign}, should we change the serum and sheet-mask offers, and what results in the review make the case? | campaign: Black Friday, Beauty, 1 |
| `q-5a69071550` When we plan another anniversary sale, how should we rethink which products get airtime given the sales and returns from the last one? | When we plan another anniversary sale, how should we rethink which products get airtime given the sales and returns from {campaign}? | campaign: anniversary sale, None, 1 |

## other: only those rewritten

| Question | Rewritten | Reference |
|---|---|---|
| `q-42d4ae2300` what should we keep doing from the home black friday live? | What should we keep doing from the {campaign} live? | campaign: Black Friday, Home, 1 |
| `q-b482d79dd3` When we build the next summer electronics lineup, which products should get a bigger role or a closer look, and what results in the 2026 Summer Sale review support that? | When we build the next summer electronics lineup, which products should get a bigger role or a closer look, and what results in the {campaign} review support that? | campaign: Summer Sale, None, 1 |
| `h-05be4a3f50` How did Beauty's growth over the past year compare with Fresh Grocery's? | How did Beauty's growth over {period} compare with Fresh Grocery's? | period: last_year |
