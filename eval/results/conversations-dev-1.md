# Conversation evaluation run: conversations-dev-1

16 questions from `eval/conversations-dev.jsonl`, through app/query_flow.py with each follow-up's turns, one at a time, as of 2026-10-15. 49 s in all.

**Right: 15 of 16** (93.8%). **Leaks: 0** in 0 questions. Statuses: answered 12, refused 3, clarify 1.

| Kind | Questions | Right |
|---|---|---|
| follow_up | 5 | 4 |
| last_campaign | 4 | 4 |
| period | 2 | 2 |
| ask_back | 1 | 1 |
| another_department | 2 | 2 |
| as_asked | 2 | 2 |

Total ms, p50 / p95 / max: 3362 / 5113 / 5113. Cost: $0.0067, the calls that finished only.

| Question | Kind | Expected | Rewritten | Came to | Right |
|---|---|---|---|---|---|
| `conv-dev-01` And how many units did it sell? | follow_up | about Beauty Anniversary Sale (2026-09-07) | – (the model didn't rewrite it: TimeoutError) | refused | **no** |
| `conv-dev-02` And the one before that? | follow_up | about Beauty New Product Launch (2026-08-10) | What was the GMV of Beauty New Product Launch (the campaign that ran from 2026-08-10 to 2026-09-14)? | about Beauty New Product Launch (2026-08-10) | yes |
| `conv-dev-03` How did our last Black Friday convert? | last_campaign | about Grocery Black Friday Live (2025-11-12) | How did Grocery Black Friday Live (the campaign that ran from 2025-11-12 to 2025-12-09) convert? | about Grocery Black Friday Live (2025-11-12) | yes |
| `conv-dev-04` What was the ROI of our most recent campaign? | last_campaign | about Home Anniversary Sale (2026-09-04) | What was the ROI of Home Anniversary Sale (the campaign that ran from 2026-09-04 to 2026-09-24)? | about Home Anniversary Sale (2026-09-04) | yes |
| `conv-dev-05` Which product sold best in our second-to-last campaign? | last_campaign | about Electronics Anniversary Sale (2026-09-02) | Which product sold best in Electronics Anniversary Sale (the campaign that ran from 2026-09-02 to 2026-10-06)? | about Electronics Anniversary Sale (2026-09-02) | yes |
| `conv-dev-06` What was the total GMV of the last Black Friday? | ask_back | asked back: Beauty, Consumer Electronics, Fresh Grocery, Home Goods | What was the total GMV of {campaign}? | asked back: Home Goods, Beauty, Consumer Electronics, Fresh Grocery | yes |
| `conv-dev-07` How did Home Goods' last Black Friday do against its plan? | last_campaign | about Home Black Friday Live (2025-11-18) | How did Home Goods' Home Black Friday Live (the campaign that ran from 2025-11-18 to 2025-12-18) do against its plan? | about Home Black Friday Live (2025-11-18) | yes |
| `conv-dev-08` The Fresh Grocery one. | follow_up | about Grocery Black Friday Live (2025-11-12) | What was the total GMV of the Grocery Black Friday Live (the campaign that ran from 2025-11-12 to 2025-12-09)? | about Grocery Black Friday Live (2025-11-12) | yes |
| `conv-dev-09` What was the GMV of Beauty's last campaign? | another_department | refused | What was the GMV of Beauty's {campaign}? | refused | yes |
| `conv-dev-10` What about Home Goods' last campaign? | another_department | refused | Which product sold best in Home Goods' {campaign}? | refused | yes |
| `conv-dev-11` What was our total GMV last month? | period | rewritten with "2026-09-01", "2026-09-30" | What was our total GMV in September 2026 (2026-09-01 to 2026-09-30)? | answered | yes |
| `conv-dev-12` How many campaigns did we finish this year? | period | rewritten with "2026-01-01" | How many campaigns did we finish in 2026 (2026-01-01 to 2026-12-31)? | answered | yes |
| `conv-dev-13` What happens if a product fails it? | follow_up | rewritten with "label" | What happens if a product fails the food safety labeling check? | answered | yes |
| `conv-dev-14` How soon does the customer have to make it? | follow_up | rewritten with "claim" | How soon does the customer have to make a damage claim for a bulky item that arrives damaged? | answered | yes |
| `conv-dev-15` What should we change next time we run a launch? | as_asked | as asked | – (stands on its own) | answered | yes |
| `conv-dev-16` How did the Grocery Fan Appreciation Day that started on 2025-11-16 convert? | as_asked | as asked | – (stands on its own) | answered | yes |
