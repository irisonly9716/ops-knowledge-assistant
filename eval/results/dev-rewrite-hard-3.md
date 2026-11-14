# Rewrite evaluation run: dev-rewrite-hard-3

16 questions from `eval/dev.jsonl`, the rewrite alone, with each follow-up's turns. $0.0022; p50 1060 ms, p95 1491 ms.

| Group | Questions | Rewritten | With a campaign | With a period | Left as asked, by why |
|---|---|---|---|---|---|
| follow-up | 6 | 5 | 1 | 0 | the model wrote the question as it was 1 |
| relative word | 10 | 8 | 6 | 2 | stands on its own 2 |

## follow-up

| Question | Rewritten | Reference |
|---|---|---|
| `q-18906f3f3f` user: Who owns the Beauty sale event inventory check? / assistant: The Beauty Supply Chain team owns the check. / **Who signs off on it?** | Who signs off on the Beauty sale event inventory check? | – |
| `q-cde842f99a` user: Who confirms stock counts for the Beauty sale event inventory check? / assistant: Warehouse & Logistics confirms stock counts. / **who updates the stream plan if a shade gets pulled?** | – (the model wrote the question as it was) | – |
| `q-40d099213e` user: Which core numbers do we review after an Electronics livestream? / assistant: Conversion rate, demo-to-purchase rate, and returns marked “not as de / **And what do we check in the product demonstrations?** | What do we check in the product demonstrations during an Electronics livestream? | – |
| `q-8899a36ebc` user: What do we do if a wrong product spec was shown on screen? / assistant: Correct it at the start of the next stream and confirm the corrected  / **and where do we log the findings?** | Where do we log the findings about a wrong product specification shown on screen? | – |
| `q-7cb370d465` user: When was the most recent finished Home Goods New Product Launch? / assistant: Home New Product Launch ran from 2026-03-13 to 2026-04-01. / **And the one before that?** | When was the {campaign} before the most recent finished Home Goods New Product Launch? | campaign: New Product Launch, Home Goods, 2 |
| `q-c6e9e26d00` user: When did the Electronics Black Friday Live campaign run? / assistant: Electronics Black Friday Live ran from 2025-11-16 to 2025-12-12. / **And the Beauty one?** | When did the Beauty Black Friday Live campaign run? | – |

## relative word

| Question | Rewritten | Reference |
|---|---|---|
| `q-2b3264c04e` What does the Sale Event Inventory Check require if the previous batch’s defect rate was above 2%? | – (stands on its own) | – |
| `q-22b362d5b5` In the Consumer Electronics sale-event inventory check, what happens if the previous batch's defect rate was above 2%? | – (stands on its own) | – |
| `q-cd428e41fe` How much GMV did last Black Friday's electronics livestream bring in? | How much GMV did {campaign} bring in? | campaign: Black Friday, electronics, 1 |
| `q-123c6ee65c` What was the conversion rate for our most recent product launch? | What was the conversion rate for {campaign}? | campaign: product launch, None, 1 |
| `q-617dabba49` How much GMV did the latest fan appreciation campaign generate? | How much GMV did {campaign} generate? | campaign: fan appreciation campaign, None, 1 |
| `q-b66ecb8a0e` What was the average conversion rate for last Black Friday's grocery live? | What was the average conversion rate for {campaign}'s grocery live? | campaign: Black Friday, grocery, 1 |
| `q-fa3c627a7f` Which of our grocery campaigns this year had the highest peak live audience, and who hosted it? | Which of our grocery campaigns in {period} had the highest peak live audience, and who hosted it? | period: this_year |
| `q-1c63e12ded` What was the highest peak concurrent viewer count among campaigns that started in the last three full months? | What was the highest peak concurrent viewer count among campaigns that started in {period}? | period: last_full_months 3 |
| `q-e9a1a5276e` For the next sale like last Beauty Black Friday, should we change the serum and sheet-mask offers, and what results in the review make the case? | For the next sale like {campaign}, should we change the serum and sheet-mask offers, and what results in the review make the case? | campaign: Black Friday, Beauty, 1 |
| `q-5a69071550` When we plan another anniversary sale, how should we rethink which products get airtime given the sales and returns from the last one? | When we plan another anniversary sale, how should we rethink which products get airtime given the sales and returns from {campaign}? | campaign: anniversary sale, None, 1 |
