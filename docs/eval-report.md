# Evaluation report

**Draft.** Numbers from the `dev` split only: the baseline with vector search alone, reranking compared against it,
and the zero-shot Text-to-SQL baseline. Hybrid search and the final `test` numbers come later. Every number below
comes from a file in [eval/results/](../eval/results/), and every table can be rebuilt from the `.jsonl` next to it,
except the comparison of reranking models, which says where it comes from.

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
them, written by hand, come next. Reranking doesn't change it: the threshold is applied to the best vector score,
before reranking, and the sweep on the reranked runs picks 0.14 again (see "Reranking").

## Replies that cite nothing

In the five runs (540 queries) there were 6 errors, and every one was the model refusing in its own words ("the
excerpts don't say ...") instead of with the fixed word. With no citation, the reply failed validation and the user
would have got a 502. Such a reply is now shown as the standard refusal and logged as a warning. Nothing uncited is
shown either way. A citation of an excerpt that doesn't exist is still an error, because that is the model making
something up.

## Reranking: bge-reranker-v2-m3

The search returns 50 candidates in vector order, a cross-encoder reads the question together with each of them and
reorders them, and the prompt takes the first 5. The model runs in Text Embeddings Inference (TEI), a service of its
own (`app/rag/reranking.py` says why). It only sees chunks that passed the department filter. The refusal threshold is
applied to the best vector score before reranking: the reranker's scores aren't on the cosine scale, and a question far
from every document has nothing worth reordering.

### Which model, and how many candidates

Measured once, on the 50 candidates of the baseline run for the 66 questions with labelled sections, with each model in
PyTorch on the development CPU (6 cores). The script was a one-off and isn't in the repository; its bge row at 50
candidates matches the runs through the service below, which are.

| Order | Candidates | Recall@5 | MRR | Recall@5, paraphrase | MRR, paraphrase | p50 a query, CPU |
|---|---|---|---|---|---|---|
| Vector search | | 80.3% | 0.743 | 78.3% | 0.840 | |
| ms-marco-MiniLM-L6-v2 | 30 | 88.7% | 0.802 | 72.5% | 0.642 | 1.1 s |
| ms-marco-MiniLM-L6-v2 | 50 | 88.7% | 0.802 | 72.5% | 0.642 | 1.9 s |
| bge-reranker-v2-m3 | 30 | 92.7% | 0.920 | 80.4% | 0.833 | 29 s (3 queries) |
| **bge-reranker-v2-m3** | **50** | **92.7%** | **0.913** | 80.4% | 0.833 | 44 s |

- **bge over MiniLM:** 4 more points of Recall@5 and 0.11 more MRR. MiniLM, trained on web search queries, ranked
  paraphrased questions worse than vector search did; bge didn't. bge is about 25 times larger, which is why it runs
  on a GPU in its own service. It also reads other languages; the corpus is English, so that isn't measured here.
- **50 candidates over 30:** the same Recall@5, but at 30 two labelled sections (in two questions) were never
  candidates, so no reranker could have raised them: vector search's Recall@30 is 98.9%, its Recall@50 100%. MRR is
  0.007 lower at 50, one more wrong chunk getting ahead. The corpus will grow and push sections further down the vector
  order, so the margin is worth more than the 0.007. Each candidate costs the reranker time, which matters only once
  its latency on a GPU is known.

### Through the service: two runs with reranking

The same instructions, run twice with and twice without (the runs of "The answer instructions"). TEI `cpu-1.9.4` on the
same CPU; the evaluation waited up to 120 s for it (`--rerank-timeout`), so every question was reranked.

| | Without, run 1 | Without, run 2 | With, run 1 | With, run 2 |
|---|---|---|---|---|
| Recall@5 / MRR of what the prompt got | 80.3% / 0.743 | 80.3% / 0.743 | 92.7% / 0.913 | 92.7% / 0.913 |
| Answered / refused by the model (of 54) | 46 / 8 | 47 / 7 | **53 / 1** | **53 / 1** |
| Wrong answers to the 15 questions to refuse | 0 | 0 | 0 | 0 |
| Faithfulness | 100% | 100% | 99.6% | 96.5% |
| Coverage, every graded answer | 84.1% | 82.1% | 88.9% | 85.3% |
| Coverage, the 45 answers graded in all four runs | 83.7% | 83.5% | 88.3% | 84.1% |
| Input tokens a query | 1,136 | 1,136 | 1,389 | 1,389 |

- **The gain is in what gets answered.** Seven answerable questions that both runs without reranking refused were
  answered in both runs with it. In each, a labelled section had ranked 6th to 21st, or 41st, and the model, as
  instructed, refused rather than answer from excerpts that didn't hold it. Reranking put the section first or second
  in six; those six were answered with every answer point. The seventh asks about two documents: its second section
  rose from 41st to 12th, still outside the prompt, and it was answered for the half it had.
- **The one still refused** also needs two documents: its second section rose from 19th to 6th, one short of the
  prompt, and the model refused. Putting more excerpts in the prompt would answer it; that is a separate choice.
- **Answers that were already given didn't get better:** on the 45 graded in all four runs, coverage moved within
  the run-to-run noise.
- **Faithfulness is within the noise too.** Four answers had unsupported claims across the two runs. One is the
  grader's known strictness (it marked "Cold-storage capacity can handle the sale's order spike" as unsupported by a
  step to confirm cold-storage capacity). Three are the answer adding or dropping a condition: the Pricing team
  approves price changes only after the 24-hour lock; the price paid replaces the list price only for livestream-only
  prices; and an added "before featuring it prominently". The instructions' 100% without reranking rested on two or
  three claims; the original instructions scored 96.5% and 96.8%.
- **One answer couldn't be graded,** the same one in both runs: in one of its two checking steps the grader returned
  an empty list of verdicts for 3 items. Without reranking it was graded, with different excerpts. It counts as
  ungraded, not as a score; it wasn't investigated further.
- **Wrong answers stay at 0,** and the threshold sweep is the same as without reranking apart from the wrong refusals:
  1 of 54 at every threshold from 0 to 0.25. 0.14 stays.
- **Keyword questions** reach Recall@5 100% after reranking, against 92.7% for all: still no case for hybrid search on
  this corpus.
- **The prompt grows by about a fifth** (1,136 to 1,389 input tokens): the excerpts the reranker puts first are longer
  on average. $0.00016 a query instead of $0.00013.
- **The order is the same every time and matches PyTorch.** Both runs reranked every question identically. Against the
  PyTorch measurement, 2 of 66 questions had a section one place apart (3rd instead of 2nd, 20th instead of 21st),
  none across the 5th, so Recall@5 and MRR are the same. TEI's CPU backend computes GeLU with an approximation, so its
  scores aren't exactly PyTorch's, and close calls can swap.

### Latency: not measured where it would run

On the development CPU, through TEI, reranking 50 candidates took 42.6 s at p50 and 51.1 s at p95 (the first run; the
second took 49.9 s at p50, most likely because other work on the machine took CPU from it). The answering model took
about as long as without reranking: p50 1.3 s against 1.2 s, with the longer prompt. In production the reranker runs
on a GPU, and that hasn't been measured, so whether reranking fits in the 3 s target isn't known. At the 300 QPS
peak it would have to score 15,000 question-chunk pairs a second (300 × 50).

Until it is measured, a query waits at most 0.5 s for the reranker (`RERANK_TIMEOUT_SECONDS`) and then goes on in vector
order with a warning. Checked through the real app on this CPU: the query fell back after the timeout and was answered;
with the reranker stopped, the same, the refused connection showing up as the timeout because Windows takes about 2 s
to give up on one. Not checked: whether TEI keeps computing a request after the client has given up on it. If it does,
the timeout doesn't take load off an overloaded reranker.

## Text-to-SQL: the zero-shot baseline

```bash
uv run python -m evaluation.sql_run --split dev --name zero-shot-1   # needs Postgres with the mock data and V9
```

The 39 questions of `dev` with a gold SQL go to the baseline agent ("Text-to-SQL: the baseline" in
[llm-service/README.md](../llm-service/README.md)): LangChain's `create_agent` with gpt-6-luna and tools to list the
tables, see their columns and run a query. Its instructions say who asks, what they may see and today's date; no data
dictionary, no views, no examples. A question is scored on the rows of the last query that ran. Two runs.

| Questions | n | Execution accuracy, run 1 / 2 | Strict | Gold numbers found |
|---|---|---|---|---|
| data: the number is the answer | 27 | 66.7% / 70.4% | 55.6% / 55.6% | 80.0% / 80.0% |
| both: numbers that support an answer | 12 | 58.3% / 58.3% | 50.0% / 50.0% | 73.3% / 84.2% |

3 of the 39 questions changed between the runs. Why the 9 data questions of run 1 are wrong:

| Why | Questions | What would fix it |
|---|---|---|
| ROI computed as (GMV − operating cost) / operating cost, the textbook marketing ROI, instead of profit / operating cost | all 4 ROI questions, in both runs | the data dictionary (profit already nets product cost) and the `roi` column of `campaign_summary` |
| A follow-up asked without the turn before it | 2 | conversation memory |
| "The conversion rate of our launch" read as the rate of its last day | 1 | `avg_conversion_rate` in `campaign_summary` |
| An average with two definitions, one given | 1 | the data dictionary or examples; the dataset asks for both |
| All products sorted, the answer first, instead of the one asked for | 1, run 1 only | nothing: execution accuracy scores the rows, not the reply |

Run 2 got the sorted list right and missed nothing run 1 got among the data questions.

- **Three gold answers were corrected before these runs**, through the review page, which runs the corrected SQL and
  stores what the database returns. One average was in percent rounded to two places (8.09, 8.23), which no query
  returning the stored rates could match; two queries on both documents and data returned `campaign_id`, a number no
  question asks for and the scorer then required. Two earlier runs on the old gold scored the data questions 66.7%
  both times; these runs replace them.
- **Strict against lenient.** 18 and 19 of the 27 data questions return the gold's numbers, 15 return its names and
  dates too. The others return the number without saying which campaign it is from, which answers the question.
- **Every final query for staff and managers named the asker's department**: 31 of 31 and 32 of 32 (one question of
  run 1 was stopped at the limit of model calls before a final query). The instructions are followed here, but they
  are not a safeguard: the filter code adds and row-level security are ([permission-model.md](permission-model.md)).
- **Questions on both documents and data** are an imperfect fit for execution accuracy: their gold is one choice of
  supporting numbers, so the share of the gold's numbers found is the fairer reading. One of the two corrected ones is
  still wrong in both runs, for the ROI.
- **Latency and cost.** p50 8.9 s and 8.4 s, p95 28 s and 34 s, one question at a time; 4 model calls a question at the
  median, 8 at most, because the agent lists the tables and reads their columns before every query. About $0.001 a
  question, about 6 times a document answer with reranking ($0.00016). Far over the 3 s a query has.

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
- **Answers to questions about numbers** aren't graded: Text-to-SQL is scored on its rows, not on the reply. Follow-ups
  aren't either until there is conversation memory.
- **Latency** is one query at a time on a development machine, not under load. The reranker's was measured only on
  a CPU, where it can't be used.

## The runs

| File | Instructions | Note |
|---|---|---|
| `dev-baseline` | original | the baseline above |
| `dev-baseline-rerun` | original | noise; ran alongside another run, so its latency isn't used |
| `dev-prompt-v2` | "answer only from what the excerpts cover" | rejected |
| `dev-prompt-v3` | now in the code | spot-check file for the grader |
| `dev-prompt-v3-rerun` | now in the code | noise; ran alongside another run |
| `dev-rerank-1` | now in the code | with reranking |
| `dev-rerank-2` | now in the code | with reranking; noise; reranked slower than run 1, so its latency isn't used |
| `dev-sql-zero-shot-1` | the SQL baseline's | Text-to-SQL, zero-shot |
| `dev-sql-zero-shot-2` | the SQL baseline's | Text-to-SQL, zero-shot; noise |

Each run is a `.jsonl` of what every question went through, an `.md` of its tables with the instructions it used, and a
spot-check file of 30 graded answers picked at random, except the SQL runs, which need no grader. The seven document
runs cost about $1.98, $1.87 of it grading; the two SQL runs $0.08, and the two they replace $0.06.
