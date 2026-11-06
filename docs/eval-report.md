# Evaluation report

**Draft.** Numbers from the `dev` split only, with vector search alone. Reranking, hybrid search and the final `test`
numbers come later and will be compared against this baseline. Every number below comes from a file in
[eval/results/](../eval/results/), and every table can be rebuilt from the `.jsonl` next to it.

```bash
cd llm-service
uv run python -m evaluation.run --split dev --name baseline   # needs Postgres with the corpus, OPENAI_API_KEY
```

## What is measured, and how

The run sends every question of a split through the same `QueryService` that serves `/query`, in-process, one at a
time. Three things make it more than a replay of `/query`:

- **The search returns the top 50**, not the 5 the prompt gets, so a section ranked too low can be told apart from one
  the search never found.
- **The threshold is 0**, so every question reaches the model. At any threshold t, a question whose best chunk scores
  below t would have been refused before the model and every other one ends as it did. The whole threshold sweep comes
  from one run.
- **Answers are graded by a second model** (gpt-6-sol; answers come from gpt-6-luna), the way RAGAS does it:
  *faithfulness* splits an answer into claims and checks each against the excerpts the answering model had;
  *coverage* checks each of the question's reviewed answer points against the answer. RAGAS itself isn't used: its
  current release needs `instructor`, and every `instructor` that accepts openai 3.x pins a `jiter` older than the one
  the openai 3.29 used here needs. The method is short and is in `llm-service/evaluation/judge.py`.

| | Questions in dev | Used for |
|---|---|---|
| Documents, to answer | 54 | retrieval, answers, threshold |
| Documents and data, to answer | 12 | retrieval only: their answer points include numbers from SQL |
| Data, to answer | 27 | nothing yet: Text-to-SQL isn't built |
| To refuse (7 near misses, 6 unauthorized, 2 out of scope) | 15 | threshold, and that nothing hidden is retrieved |

Retrieval is scored by section: a label names a section, a section can be several chunks, and it counts as found at the
rank of its best chunk. Recall@k is the share of a question's labelled sections in the first k, averaged over
questions; MRR is 1 / the rank of the first one found.

## Baseline: vector search, the original answer instructions

### Retrieval

| Questions | n | Recall@5 | Recall@50 | MRR |
|---|---|---|---|---|
| All with labelled sections | 66 | 80.3% | 100.0% | 0.743 |
| Documents | 54 | 84.3% | 100.0% | 0.731 |
| Documents and data | 12 | 62.4% | 100.0% | 0.794 |
| Direct | 27 | 79.9% | 100.0% | 0.722 |
| Keyword | 12 | 83.3% | 100.0% | 0.661 |
| Paraphrase | 12 | 78.3% | 100.0% | 0.840 |
| Colloquial | 14 | 78.6% | 100.0% | 0.750 |
| Cross-document | 7 | 78.6% | 100.0% | 0.929 |
| Follow-up | 4 | 50.0% | 100.0% | 0.348 |
| Planning advice | 6 | 80.6% | 100.0% | 1.000 |

- **Every labelled section is in the top 50, and a fifth of them miss the top 5.** What the search finds is fine; the
  order is what loses answers. Of the 6 answerable questions the model refused, all 6 had a labelled section ranked 6th
  to 21st. That is what a reranker is for: it reorders the candidates the search already found.
- **Keyword questions aren't worse**: Recall@5 83.3% against 80.3% for all. The rule for bringing in hybrid search was
  a gap of 10 points or more, or Recall@50 clearly short; neither holds. With only 12 keyword questions, one question
  moves that row by 8 points, and Recall@50 at 100% says the corpus is still easy to search: whether hybrid search is
  needed is decided again once the corpus and the questions are harder.
- **Follow-ups** are asked without the turns before them, because there is no conversation memory yet; their 50% is
  that, not the search.
- **Nothing hidden was retrieved**: for no question was a section the asker may not see among the 50 candidates.

### Answers

| | Run 1 | Run 2 |
|---|---|---|
| Answered / refused by the model / error (of 54) | 47 / 6 / 1 | 48 / 6 / 0 |
| Faithfulness: claims the excerpts support | 96.5% | 96.8% |
| Answers with every claim supported | 44 / 47 | 45 / 48 |
| Coverage: answer points said | 84.9% | 81.8% |
| Wrong answers to the 15 questions to refuse | 1 | 1 |

Both runs answered the same near miss: asked whether a gift substitution is announced before packing or before
shipping, the model answered with what the document says about announcing it, which isn't the question.

### Latency and cost

One query at a time, in llm-service without the backend, on a development machine where every search also pays about
44 ms of Docker Desktop port forwarding. Not a load test: the 300 QPS design is still to be load tested.

| ms | p50 | p95 | max |
|---|---|---|---|
| Embedding + search | 244 | 336 | 605 |
| Whole query | 1,396 | 2,626 | 4,971 |
| Model alone | 1,155 | 2,278 | 4,710 |

About 975 input and 38 output tokens a query: $0.00012 a query with the model always asked, $0.12 a thousand.
Grading a run costs about $0.25, twenty times the run's answers.

## Run-to-run noise

The answering model doesn't give the same reply every time. Two runs of the same instructions ended differently on 4
of the 108 questions (1 or 2 of the 54 answerable from documents) and differed by about 3 points in coverage, so a
change smaller than that is not a change. Both versions compared below were run twice.

## The answer instructions

The reviewed dataset expects two things the original instructions didn't ask for: when the excerpts hold more than one
right answer, give each with its citation and say why there are several; when a question draws on another
department, answer only from what the asker may see and say the other department's material wasn't used.

| Instructions | Wrong answers (of 15) | Faithfulness | Coverage |
|---|---|---|---|
| Original, two runs | 1, 1 | 96.5%, 96.8% | 84.9%, 81.8% |
| "Answer only from what the excerpts cover", one run | 3 | 98.6% | 87.2% |
| **Now in the code**, two runs | **0, 0** | **100%, 100%** | 84.1%, 82.1% |

The middle version made it worse: the model took "answer from what the excerpts cover" as licence to answer near
misses with whatever was close ("no specific lead time is stated, but the checklist must be done before going live").
The version in the code says outright that excerpts on the right topic without the specific thing asked are not an
answer, and keeps the other-department rule to questions that ask about another department. Coverage moved within the
noise. The improvement rests on few questions: one near miss in the wrong-answer column, and two or three claims in
faithfulness.

## The refusal threshold: 0.14

| Threshold | Wrong answers (of 15) | Wrong refusals (of 54) |
|---|---|---|
| 0.00 to 0.27 | 0 | 8 (run 1), 7 (run 2) |
| 0.35, the previous value | 0 | 9, 8 |
| 0.45 | 0 | 12, 12 |
| 0.55 | 0 | 20, 20 |

With the instructions now in the code. Cost is weighed on the rates, a wrong answer counting twice a wrong refusal:
how often each kind of question comes up in real use isn't known, so the two are taken as about as common.

**Why 0.14:**

1. **The threshold doesn't stop wrong answers here; the model does.** The questions to refuse scored 0.33 to 0.65 and
   the answerable ones 0.27 to 0.70. No threshold up to the lowest answerable question stops a single question to
   refuse, so a wrong answer costing more than a wrong refusal doesn't push the threshold up: there is nothing below it
   to stop. Every question to refuse was refused by the model in both runs, one of them in its own words (see
   "Replies that cite nothing").
2. **What is left is saving a model call** on questions far from everything. Eight questions unrelated to the company
   (the weather, a poem, the World Cup, a Wi-Fi router, random letters, and so on; probes, not part of the dataset)
   scored 0.10 to 0.25.
3. **Above 0.27 it starts refusing real questions.** The previous 0.35 refused "what counts as a private msg?" (0.27),
   which the model answered correctly in both runs, every claim supported and every answer point said.
4. **So the threshold stays well clear of answerable questions.** 0.14 is the middle of the range of lowest cost; it
   stops the farthest of the probes and leaves the rest to the model, which refuses them. A threshold just under 0.27
   would stop all eight, saving about $0.0001 and a second each, at the risk of refusing the next answerable question
   that scores 0.26. A refused real question costs more than a model call.

**What would change it:** questions out of scope are the weakest part of the sample (2 in dev, both 0.33). More of
them, written by hand, and a threshold sweep after reranking, whose scores are on a different scale, come next.

## Replies that cite nothing

In the five runs (540 queries) there were 6 errors, and every one was the model refusing in its own words ("the
excerpts don't say ...") instead of with the fixed word. With no citation, the reply failed validation and the user
would have got a 502. Such a reply is now shown as the standard refusal and logged as a warning. Nothing uncited is
shown either way. A citation of an excerpt that doesn't exist is still an error, because that is the model making
something up.

## What these numbers don't show

- **Small samples.** 54 answerable questions, 15 to refuse, 12 keyword questions, 2 out of scope. One question is 2 to
  8 points in most rows.
- **Generated questions borrow the documents' words.** All but three questions were written by a model from the
  documents, which flatters retrieval; questions written by hand in everyday language come next.
- **The grader isn't checked yet.** A person is checking 30 graded answers
  ([dev-prompt-v3-spot-check.md](../eval/results/dev-prompt-v3-spot-check.md)); until then the grades are the model's
  word. On a few answers seen so far it is strict: it marked "the warehouse must have enough cold-storage capacity" as
  unsupported by "confirm cold-storage capacity".
- **`test` hasn't been run.** It reports the final numbers once, after the choices above are made on `dev`.
- **Questions about numbers** aren't graded until Text-to-SQL exists, and follow-ups until there is conversation memory.
- **Latency** is one query at a time on a development machine, not under load.

## The runs

| File | Instructions | Note |
|---|---|---|
| `dev-baseline` | original | the baseline above |
| `dev-baseline-rerun` | original | noise; ran alongside another run, so its latency isn't used |
| `dev-prompt-v2` | "answer only from what the excerpts cover" | rejected |
| `dev-prompt-v3` | now in the code | spot-check file for the grader |
| `dev-prompt-v3-rerun` | now in the code | noise; ran alongside another run |

Each run is a `.jsonl` of what every question went through, an `.md` of its tables with the instructions it used, and a
spot-check file of 30 graded answers picked at random. The five runs cost about $1.35, $1.27 of it grading.
