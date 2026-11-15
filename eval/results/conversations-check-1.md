# Conversation evaluation run: conversations-check-1

16 questions from `eval/conversations.jsonl`, through app/query_flow.py with each follow-up's turns, one at a time, as of 2026-10-15. 51 s in all.

**Right: 16 of 16** (100.0%). **Leaks: 0** in 0 questions. Statuses: answered 11, refused 4, clarify 1.

| Kind | Questions | Right |
|---|---|---|
| follow_up | 5 | 5 |
| last_campaign | 4 | 4 |
| period | 2 | 2 |
| ask_back | 1 | 1 |
| another_department | 2 | 2 |
| as_asked | 2 | 2 |

Total ms, p50 / p95 / max: 3454 / 4155 / 4155. Cost: $0.0072, the calls that finished only.

| Question | Kind | Expected | Rewritten | Came to | Right |
|---|---|---|---|---|---|
| `conv-01` How much profit did it make? | follow_up | about Electronics New Product Launch (2026-09-29) | How much profit did Electronics New Product Launch (the campaign that ran from 2026-09-29 to 2026-10-13) make? | answered | yes |
| `conv-02` What about the one before it? | follow_up | about Home Back to School (2026-08-03) | What was the GMV of Home Back to School (the campaign that ran from 2026-08-03 to 2026-08-31)? | about Home Back to School (2026-08-03) | yes |
| `conv-03` When did our last Fan Appreciation Day end? | last_campaign | about Beauty Fan Appreciation Day (2026-06-13) | When did Beauty Fan Appreciation Day (the campaign that ran from 2026-06-13 to 2026-07-14) end? | about Beauty Fan Appreciation Day (2026-06-13) | yes |
| `conv-04` What was the conversion rate of our latest campaign? | last_campaign | about Grocery Anniversary Sale (2026-09-10) | What was the conversion rate of Grocery Anniversary Sale (the campaign that ran from 2026-09-10 to 2026-09-30)? | about Grocery Anniversary Sale (2026-09-10) | yes |
| `conv-05` How much did we spend on our third most recent campaign? | last_campaign | about Home Summer Sale (2026-06-10) | How much did we spend on Home Summer Sale (the campaign that ran from 2026-06-10 to 2026-07-24)? | about Home Summer Sale (2026-06-10) | yes |
| `conv-06` Which product sold best in the last Summer Sale? | ask_back | asked back: Beauty, Consumer Electronics, Fresh Grocery, Home Goods | Which product sold best in {campaign}? | asked back: Home Goods, Beauty, Fresh Grocery, Consumer Electronics | yes |
| `conv-07` What was the GMV of Consumer Electronics' last Anniversary Sale? | last_campaign | about Electronics Anniversary Sale (2026-09-02) | What was the GMV of Consumer Electronics' Electronics Anniversary Sale (the campaign that ran from 2026-09-02 to 2026-10-06)? | about Electronics Anniversary Sale (2026-09-02) | yes |
| `conv-08` Beauty's. | follow_up | about Beauty Summer Sale (2026-06-12) | Which product sold best in Beauty Summer Sale (the campaign that ran from 2026-06-12 to 2026-07-21)? | about Beauty Summer Sale (2026-06-12) | yes |
| `conv-09` How many units did Consumer Electronics sell in their last campaign? | another_department | refused | How many units did Consumer Electronics sell in {campaign}? | refused | yes |
| `conv-10` And Fresh Grocery's? | another_department | refused | What was the GMV of {campaign}? | refused | yes |
| `conv-11` What was our GMV over the last three full months? | period | rewritten with "2026-07-01", "2026-09-30" | What was our GMV over the 3 full months from 2026-07-01 to 2026-09-30? | answered | yes |
| `conv-12` How many units did we sell last year? | period | rewritten with "2025-01-01", "2025-12-31" | How many units did we sell in 2025 (2025-01-01 to 2025-12-31)? | answered | yes |
| `conv-13` Who do we tell if it still fails after that? | follow_up | rewritten with "mirroring" | Who do we tell if the screen mirroring still fails after that? | answered | yes |
| `conv-14` And how is their performance reviewed after that? | follow_up | rewritten with "host" | How is a new Beauty host's performance reviewed after their 60-day probation with regular coaching on product knowledge and stream execution? | refused | yes |
| `conv-15` Should the previous batch of chargers be counted first when we restock? | as_asked | as asked | – (stands on its own) | refused | yes |
| `conv-16` What was the ROI of the Home New Product Launch that started on 2026-01-24? | as_asked | as asked | – (stands on its own) | answered | yes |
