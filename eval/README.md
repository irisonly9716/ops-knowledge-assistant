# The evaluation dataset

Questions to measure the assistant on, one per line of a JSONL file. `dev.jsonl` and `test.jsonl` are the
dataset: 257 reviewed questions. `candidates.jsonl` holds everything a model wrote from the corpus in `sop_docs/`
and `campaign_docs/`, with each question's review status and the reason for it, plus the questions written by hand.
The format is defined, and checked on every load, by `llm-service/evaluation/dataset.py`.

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
| `hidden` | sections the asker may not see that the question is about: they must not be retrieved for this asker |
| `related` | the document a near miss is close to, without its answer |
| `gold_sql`, `gold_result` | for a question about numbers: SQL that keeps to the asker's department, and what it returned, every value as text so it stays exact |
| `answer_points` | what a right answer has to say. For a question about numbers, the numbers themselves are in `gold_result` |
| `tags` | how it is asked (`direct`, `keyword`, `paraphrase`, `colloquial`, `vague`), what makes it hard (`cross_document`, `near_miss`, `unauthorized`, `out_of_scope`, `general_knowledge`, `multi_turn`, `planning_advice`), and what its SQL needs (`single_table`: one kind of data, `multi_table`, `time_range`) |
| `group`, `split` | where it came from, and whether it is in `dev` or `test` |
| `source` | `generated:<model>`, or `human` for a question written by hand |
| `status`, `note` | `candidate` until reviewed, then `accepted` or `rejected`, and why |

**Sections are named by what they are, not by id.** An SOP by department and title, a plan or review by its
campaign's department, name and start date, and a section by its heading path. Chunk ids change every time the
corpus is ingested again or the chunking changes, and labels that stored them would all point at the wrong text.
`evaluation.dataset.resolve()` finds today's chunks of a section.

## dev and test

About 40% of the questions are in `dev` and 60% in `test`, decided by a hash, so a question never changes side
when others are added:

- **A question to answer goes with its group.** Questions written from the same section or about the same SQL share
  a group and land on the same side: otherwise a reworded question in `test` would have its answer shown in `dev`.
- **A refusal is split on its own.** It has no answer to show. Split by group, the 39 generated refusals came from 12
  groups and 32 of them landed in `test`, which left too few in `dev` to calibrate the refusal threshold on.
- **A refusal written by hand goes to `dev`**, for the same reason.

| | dev | test |
|---|---|---|
| All | 108 | 149 |
| To answer / to refuse | 93 / 15 | 124 / 25 |
| Near miss / unauthorized / out of scope | 7 / 6 / 2 | 7 / 11 / 8 |
| Cross-document / follow-up / planning advice | 7 / 6 / 6 | 14 / 10 / 10 |
| Keyword / paraphrase / colloquial / vague | 15 / 17 / 18 / 12 | 23 / 31 / 26 / 12 |
| Documents / data / both / none | 66 / 28 / 12 / 2 | 93 / 34 / 14 / 8 |

`dev` has only 2 out-of-scope questions; refusals written by hand are where more come from.

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

256 candidates, 2 of the first 257 dropped by these checks; about $0.70 in all. As written, before the review and
before refusals were split on their own:

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

## Reviewing

```bash
cd llm-service
uv run python -m evaluation.review   # http://127.0.0.1:8765
```

The page shows each candidate with the text it cites and its SQL result, and saves every change to
`candidates.jsonl` after checking it. Export writes the accepted questions to `dev.jsonl` and `test.jsonl`.

**How the candidates were reviewed.** A model made a first pass, writing a suggestion into every note, and the
reviewer then judged 20 of them blind, without seeing the suggestions. On 10 chosen at random they agreed on all 10:
every one the first pass suggested accepting. On 10 the first pass found hard, they agreed on 3: the first pass was
stricter about wording and realism and would have removed leading or terse questions people do ask. So the first
pass's suggestions to accept were taken as they were, and every suggestion to remove or change a question went back
to the reviewer. Agreement measured blind is how a review of this kind is checked: by feel, a reviewer agrees with
whatever they are shown.

Rules decided while reviewing, each with the question that raised it in its `note`:

- **General knowledge is refused, for now.** "What does gross margin mean?" is answered by no company document, so
  the right answer is a refusal. Such questions also carry `general_knowledge`, so that once a router sends general
  questions to an answer labelled as not from company sources, their expected outcome can change without rewriting
  them. Tax, legal and HR questions stay refused either way: they belong to the teams that own them.
- **When a question has more than one right answer the asker may see, the answer gives each.** It cites each and
  says why there are several. Two documents a Fresh Grocery employee can see set different shelf-life rules, one for
  the sale and one for shipping; an average conversion rate across campaigns can average each campaign's average or
  every day together, and the gold SQL returns both.
- **What the asker may not see is neither used nor guessed at.** A Beauty employee asking to compare Beauty's growth
  with Fresh Grocery's gets a refusal. A Fresh Grocery manager asking to estimate GMV "the way Beauty does it" gets
  the company-wide practice, and the answer says it doesn't include Beauty's. A question about a topic only other
  departments' SOPs cover is answered from the asker's own department and company-wide documents, or refused.
- **A step without its how is not an answer.** A document that says "confirm the gift is missing" doesn't say how
  to confirm it, so a question about how is a near miss and should be refused.
- **A question must stand on its own.** One that refers to "these products" with no earlier turn is removed, and one
  about a product sold in several campaigns names the campaign.
- **A vague reference the documents themselves use is kept**, tagged `vague`: "the department's biggest holiday
  shopping livestream" is how a plan describes its Black Friday Live.
- **A question that makes correct data look wrong is removed.** The customer table holds a sample of customers, so
  its total never matches product sales.
