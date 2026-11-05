# The evaluation dataset

Questions to measure the assistant on, one per line of a JSONL file. `candidates.jsonl` holds the candidates a
model wrote from the corpus in `sop_docs/` and `campaign_docs/`; a person reviews them, corrects them and adds
questions of their own before they become the dataset. The format is defined, and checked on every load, by
`llm-service/evaluation/dataset.py`.

## What a question says

```json
{"id": "q-0d3c1b7e9a", "question": "can ups ship nail polish by air?",
 "asker": {"department": "Beauty", "role": "staff"},
 "route": "none", "expected": "refuse",
 "tags": ["colloquial", "out_of_scope"], "source": "generated:gpt-6-sol",
 "group": "out_of_scope:general knowledge", "split": "test"}
```

| Field | Meaning |
|---|---|
| `question`, `turns` | the question, and for a follow-up the conversation before it |
| `asker` | who asks: the department and role decide what they may see, as in the service |
| `route` | `documents`, `data`, `both`, or `none` for a question nothing in the company answers |
| `expected` | `answer`, or `refuse` for a near miss, a question about something the asker may not see, and anything out of scope |
| `relevant` | the sections a right answer cites |
| `hidden` | the sections an unauthorized question is about: they must not be retrieved for this asker |
| `related` | the document a near miss is close to, without its answer |
| `gold_sql`, `gold_result` | for a question about numbers: SQL that keeps to the asker's department, and what it returned, every value as text so it stays exact |
| `answer_points` | what a right answer has to say. For a question about numbers, the numbers themselves are in `gold_result` |
| `tags` | how it is asked (`direct`, `keyword`, `paraphrase`, `colloquial`, `vague`), what makes it hard (`cross_document`, `near_miss`, `unauthorized`, `out_of_scope`, `multi_turn`, `planning_advice`), and what its SQL needs (`single_table`: one kind of data, `multi_table`, `time_range`) |
| `group`, `split` | where it came from, and so whether it is in `dev` or `test` |
| `status` | `candidate` until reviewed, then `accepted` or `rejected` |

**Sections are named by what they are, not by id.** An SOP by department and title, a plan or review by its
campaign's department, name and start date, and a section by its heading path. Chunk ids change every time the
corpus is ingested again or the chunking changes, and labels that stored them would all point at the wrong text.
`evaluation.dataset.resolve()` finds today's chunks of a section.

## dev and test

About 40% of the questions are in `dev` and 60% in `test`, decided by a hash of their `group`. Questions written
from the same section or about the same SQL share a group, so they land on the same side: otherwise a reworded
question in `test` would have its answer shown in `dev`.

**Tune on `dev` only.** Thresholds, prompts and few-shot examples are chosen with `dev`. `test` only reports the
final numbers, and none of its questions is ever shown to a model as an example: a score on questions the system
was tuned on measures how well it was tuned, not how well it works.

## How the candidates were written

```bash
cd llm-service
uv run python -m scripts.generate_questions --report   # what the candidates cover
```

`llm-service/scripts/generate_questions.py` turns the ingested corpus into 104 tasks: a few sections (or the
schema, for questions about numbers), who asks, and what kind of question it should be. gpt-6-sol writes the
questions, a different model from the one that answers (gpt-6-luna), so they aren't written in the answering
model's own words, and says which of the sections it was shown hold each answer. A question is only kept when:

- the numbers of its answer points are in the sections it cites
- its SQL is a single SELECT that runs in a read-only transaction within 5 s, returns rows, and keeps to the
  asker's department
- a question the asker must not get an answer to is about a document the asker really can't see
- a question about a campaign whose name more than one campaign has says which one, by month or date, and by the
  day when another of the name starts in the same month. Without that rule, 16 of the first 255 asked about "the
  Home New Product Launch" or "Beauty Fan Appreciation Day" with two or three to choose from, and 5 more said
  "the February Beauty Fan Appreciation Day" when two start in February 2026
- it isn't a duplicate, comparing the whole conversation of a follow-up

256 candidates, 2 of the first 257 dropped by these checks; about $0.70 in all:

| | Candidates |
|---|---|
| Route | documents 158, data 62, both 26, none 10 |
| Expected | answer 216, refuse 40 |
| How it is asked | direct 103, paraphrase 50, colloquial 44, keyword 38, vague 21 |
| What makes it hard | cross_document 20, multi_turn 16, planning_advice 16, near_miss 15, unauthorized 15, out_of_scope 10 |
| What its SQL needs | multi_table 19, time_range 15, single_table 10 |
| Asked by | staff 207, manager 31, executive 18 |
| Split | dev 101, test 155 |

**What the checks can't see.** They check that a question can be answered from what it cites, not that it is a
question anybody would ask, that its answer points are complete, or that a near miss really has no answer anywhere
in the corpus. Questions a model writes also borrow the documents' words, which makes retrieval look better than it
is. That is what the human review is for, along with 30 to 50 questions written by hand in everyday language.
