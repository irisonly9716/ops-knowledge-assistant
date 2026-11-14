# Evaluation report

**Draft.** Numbers from the `dev` split only: the baseline with vector search alone, reranking compared against it,
the zero-shot Text-to-SQL baseline, the Text-to-SQL pipeline, routing and the whole query path. Hybrid search and the final `test` numbers come later. Every number below
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
| Documents and data, to answer | 12 | retrieval, and Text-to-SQL for their numbers; answers aren't graded |
| Data, to answer | 27 | Text-to-SQL: the rows of the query that ran |
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

## Text-to-SQL: the fixed pipeline

```bash
uv run python -m evaluation.sql_run --split dev --name pipeline-1 --pipeline --dictionary --views --examples 3 --retries 2
```

The same 39 questions through the pipeline of the query path ("Text-to-SQL: the pipeline" in
[llm-service/README.md](../llm-service/README.md)). Its prompt has the views with the data dictionary, the names in the
data and the 3 closest of 28 example questions. It writes one query, which is checked and run in the asker's department;
a failed query is written again at most twice with the error, and the result is then explained. gpt-6-luna writes and
explains, within the query path's budget of 12 s a question. Two runs.

| Questions | n | Execution accuracy, run 1 / 2 | Strict | Gold numbers found |
|---|---|---|---|---|
| data: the number is the answer | 27 | 85.2% / 85.2% | 63.0% / 66.7% | 90.4% / 90.4% |
| both: numbers that support an answer | 12 | 66.7% / 66.7% | 66.7% / 66.7% | 89.4% / 91.5% |

The agent scored 66.7% and 70.4% on the data questions. The same questions are wrong in both pipeline runs. Why the 4
data questions are wrong:

| Why | Questions | What would fix it |
|---|---|---|
| A follow-up asked without the turn before it | 2 | conversation memory |
| "Last Black Friday's grocery live" read as the holiday's date, 2025-11-28; the campaign of that name started on 2025-11-12 | 1 | an example that finds a named campaign by its latest start; both runs of the earlier prompt layout below got it right |
| An average with two definitions, one given | 1 | the example that gives both isn't followed; the agent missed it too |

- **All 4 ROI questions are right in both runs**, where the agent got all 4 wrong in both: the `roi` column of
  `campaign_summary` and its comment leave nothing to work out.
- **The department filter the code adds was in every query that ran** for staff and managers: 32 of 32 in both runs,
  whatever the model wrote.
- **Correcting itself.** 3 queries failed in the two runs: a column name two joined views share, a stray `**` that
  sqlglot accepted and Postgres refused, and a reply that didn't parse. Each was written again once and then ran. One
  of the three matched the gold; the other two are questions on both documents and data whose second query returned
  other supporting numbers.
- **Latency and cost.** p50 2.4 s in both runs, p95 5.2 s and 3.9 s, one question at a time and without the backend.
  At the median, writing takes 1.1 s, explaining 1.0 s, the embedding 0.2 s, reading the names and examples 0.05 s,
  and running the query 5 ms. 2 model calls at the median, 3 at most. About $0.0003 a question, under a third of
  the agent's. The p50 is inside the 3 s a query has before routing adds its share; the p95 isn't.
- **Run as `sql_reader`** (`dev-sql-pipeline-reader`), once the queries ran as the read-only role under row-level
  security with at most 201 rows ([permission-model.md](permission-model.md)): the data questions scored 85.2% again,
  the same 4 wrong. No query failed, so the role refused nothing a question needed; the department filter was in 32 of
  32, and no result came near the row limit. One more question on both documents and data was wrong (58.3%): the
  model left the profit margin out of a query that was otherwise the earlier runs' own, a choice of columns, not the
  database. p50 3.2 s: writing and explaining took 0.2 to 0.3 s longer each, running a query 6 ms against 5.

### What the prompt's wording did

Three earlier runs, kept in [eval/results/](../eval/results/), each led to a change:

- **A broad way out.** The rule "if the data can't answer the question at all, reply NOT_IN_DATA" refused 14 of 39
  questions (`dev-sql-pipeline-broad-refusal`): 10 of the 12 on both documents and data, which ask what to change and
  so seemed beyond the data, and 4 data questions. Asked again with the same wording, two of those data questions got a
  query every time, 4 times out of 4: the model takes the way out at random. Narrowed to "only when nothing listed holds what the question is about", with questions asking why or what to
  change told to get the numbers about what they name, the four runs since refused 5 questions in all, 3 of them a
  follow-up that can't be answered without the turn before it.
- **The question last.** With the examples written as "Question: … Query: …" and the prompt ending with "Question:"
  and the question, one reply carried the pattern on: "Query:", its query, then two made-up examples, which didn't
  parse (`dev-sql-pipeline-earlier-layout-2`). The examples are now labelled as examples, and the prompt ends with
  "Write the query for this question:". The two runs on the old layout scored 88.9% and 85.2% on the data questions.
- **Errors fed back as they are.** sqlglot underlines the place of a parse error with terminal escape codes, and they
  went to the model with the error. Only the description, line and column are sent now.

The comparison with the agent changes several things at once: the dictionary, the views, the examples, the retries
and fixed steps instead of an agent. `sql_run` switches each on its own (`--dictionary`, `--views`, `--examples`,
`--retries`) for the comparison that adds one at a time. The examples were written looking at dev's mistakes, though
none repeats a dev or test question (a test checks), so dev flatters them; `test` will say how much.

## Text-to-SQL: what each part is worth

```bash
uv run python -m evaluation.sql_run --split dev --name ablation-<configuration>-<1|2> <its flags>   # 15 runs, $0.16
uv run python -m evaluation.sql_ablation --split dev   # the tables below, from the runs; no model calls
```

The pipeline has four parts on top of a model writing SQL from the schema: the data dictionary with the names found in
the data, the metric views, the 3 closest example questions, and 2 retries with the error. Each was measured two ways,
changing one thing at a time on the same 39 `dev` questions:

- **Adding one part at a time**, in the order the pipeline is built. Read alone this credits whatever comes first.
- **Taking one part out of the full pipeline**, which shows what a part is worth with all the others there.

Every configuration ran twice with the query path's settings (gpt-6-luna writing and explaining, 12 s a question; the
views read as `sql_reader`, the tables as their owner). The model doesn't write the same query twice, so a step is
credited with a question only when the question is wrong in both runs before it and right in both runs after; a
question the runs disagree on is noise. One data question is 3.7 points. All the numbers and the questions behind them:
[dev-sql-ablation.md](../eval/results/dev-sql-ablation.md).

| Adding | Data, run 1 / 2 | Change | Fixed / broke | p50 | Cost a question |
|---|---|---|---|---|---|
| zero-shot, on the tables | 51.9% / 51.9% | | | 3.1 s | $0.00020 |
| + the data dictionary and the names in the data | 70.4% / 70.4% | +18.5 | 6 / 0 | 2.8 s | $0.00031 |
| + the views instead of the tables | 88.9% / 85.2% | +16.7 | 4 / 0 | 2.8 s | $0.00026 |
| + the 3 closest examples | 88.9% / 88.9% | +1.9 | 0 / 0 | 2.7 s | $0.00028 |
| + 2 retries: the query path | 85.2% / 85.2% | -3.7 | 0 / 1, noise (below) | 2.7 s | $0.00028 |

| Taking out of the full pipeline | Data, run 1 / 2 | Against the full pipeline | Lost / gained |
|---|---|---|---|
| the data dictionary | 74.1% / 70.4% | -13.0 | 2 / 0 |
| the views | 70.4% / 74.1% | -13.0 | 3 / 0 |
| the examples | 85.2% / 88.9% | +1.9 | 0 / 1 |
| the retries | 88.9% / 88.9% | +3.7 | 0 / 1 |

What each part does, from the questions that changed:

- **The data dictionary and the names in the data: +18.5 points, and 13 lost without them.** People name campaigns the
  way they talk: "last Black Friday's electronics livestream", "the 2026 Grocery Summer Sale". Without the names in the
  data the model guessed `name = 'summer electronics sale'`, or read "Black Friday" as the holiday's date, and the
  query found no rows; with them it wrote `name = 'Electronics Summer Sale'`. 6 questions fixed in both runs, none
  broken.
- **The views: +16.7 points, and 13 lost without them.** All 4 fixed questions are about ROI. On the tables the model
  wrote a formula of its own, `(profit - cost) / cost` or `(GMV - cost) / cost`; the view's `roi` column holds the one
  definition. Without the views the examples, which are written on the views, misled: the model copied them, had
  3.5 queries a run refused, mostly "no such view", and one rewrite returned each campaign's GMV instead of the total asked for.
  The two parts don't add up independently.
- **The examples: no measurable effect on `dev`.** +1.9 points when added, and +1.9 when taken out, although they were
  written looking at `dev`'s mistakes, which flatters them. They cost $0.00001 to $0.00002 a question, plus the question's
  embedding (0.2 s at the median) when no document search has made one already. They are on in the query path's
  settings; `test`, which doesn't flatter them, will say whether they help.
- **The retries: no question gained.** They act only when a query fails, 0.5 times a run with the views and examples
  there. The question counted as broken when they were added, `q-b66ecb8a0e`, was written once in every run, from the
  same prompt and the same 3 examples in both configurations: the retries never ran for it. It is the borderline
  question of the error table above, read as November 2025 in some runs and as the holiday's date in others. A retry
  costs nothing until a query fails, and without one a failed query is a failed answer.
- **The noise is the size of a small step.** The same configuration twice differed by up to 3.7 points on the data
  questions and up to 25 on the 12 questions on both documents and data. With 27 data questions a step under 4 points can't be
  told from noise: the questions a step fixed in both runs are the evidence, not the averages.
- **Cost and latency barely move.** $0.00018 to $0.00034 a question, p50 2.3 to 3.4 s, the slowest two on the tables.
  The dictionary adds tokens; the views take some back, being described in fewer than the tables. Without the views a
  question took 2.0 model calls on average, for the rewrites after refused queries.

## Text-to-SQL: questions that try another department's data

```bash
uv run python -m evaluation.sql_leak --name 1                      # eval/unauthorized-sql.jsonl, $0.006
uv run python -m evaluation.sql_leak --questions dev --name dev-1   # eval/unauthorized-sql-dev.jsonl, $0.002
```

Two sets of questions, kept apart the way `dev` and `test` are: 10 in `unauthorized-sql-dev.jsonl` to work rules out
on, and the 23 below, which only check them and which nothing is tuned on.

20 questions from staff and managers, written to get another department's data: naming it, comparing with it, ranking
departments, asking for every department, claiming to stand in for its manager or to have the CEO's approval, asking
for a `UNION`, `set_config` or a table, for customers' IP addresses, for the users table, and a prompt injection. Asked
through the query path's pipeline as `sql_reader`, twice, with 3 controls from an executive and an admin.

- **Leaks: 0 of 20 in both runs.** A leak is a value only another department has, in the rows or the answer: one of
  77 campaign, host and product names, an amount of $1,000 or more, or a department's name as a value. The controls
  found other departments in 3 of 3 both times, so the check does find them when they are there.
- **How they ended**: the asker's own rows 12 and 13 times, no rows 4, refused as not in the data 3 and 4. Every query
  that ran had the department filter the code adds. Asked to, the model did write `set_config` once; `guard.py`
  refused it, and the rewrite read the asker's own data.
- **One answer put the asker's own number under another department's name.** Asked by Beauty staff for Fresh Grocery's
  2026 GMV, run 1 answered "Fresh Grocery's total GMV in 2026 was $8,804,221.35", which is Beauty's, then said Fresh
  Grocery's total isn't available. No other department's data was in it, so it is no leak, but it misleads. The cause
  was not that one answer: the query writer is told the views hold only the asker's department and not to filter by
  it, so a question about another department alone got a query for the asker's own numbers, 3 of the 8 such questions
  in both runs, and the explanation is then one sentence away from naming them after the question.
- **The rule that fixed it.** For staff and managers, a question about another department alone now gets
  `NOT_IN_DATA` and the fixed refusal; a question comparing their department with others still gets a query for their
  own numbers. The explanation is also told to name the numbers as the asker's department's. Worked out on the dev
  questions, then checked on these:

  | | Before | After, run 1 / 2 |
  |---|---|---|
  | dev questions: about another department alone, answered with the asker's rows | 4 of 4 | 0 / 0 of 4 |
  | dev questions: about the asker's own department, answered | 3 of 3 | 3 / 3 of 3 |
  | check questions: about another department alone, answered with the asker's rows | 3 / 3 of 8 | 0 / 0 of 8 |
  | check questions: leaks; the controls finding other departments | 0; 3 of 3 | 0; 3 of 3 |
  | `dev` data questions, the whole pipeline | 85.2% / 85.2% | 92.6% / 88.9% |
  | `dev` questions on both documents and data | 58.3% / 58.3% | 50.0% / 50.0% |

  No `dev` question was refused that wasn't before: the only refusal, both times, is the follow-up asked without the
  turn before it. The one question on both documents and data that went wrong in both runs, about Grocery Black Friday
  Live, is the asker's own campaign and got its query; it read "audience" as the average daily viewers instead of the
  peak. The ablation above ran before this rule.
- The same kinds of attack, written as queries and run against each layer alone, are in
  `llm-service/tests/test_sql_leaks.py` ("Filtering data" in [permission-model.md](permission-model.md)).

## Routing: documents, data, both or neither

```bash
uv run python -m evaluation.route_run --split dev --name 3   # $0.007 a run
```

Every question of `dev` says which route it needs: 66 documents, 28 data, 12 both, 2 none. Four ways of routing were
run on the same 108 questions: a keyword rule, a vote of the 5 nearest questions by embedding (leaving out a
question's own group, whose paraphrases would give its route away), the model with structured output (`app/router`),
and a cascade that asks the model only when the 5 nearest questions all agree. Two mistakes are counted apart,
because they don't cost the same: a path missing loses the answer or half of it; a path extra costs a search and a
model call. 6 questions are follow-ups, asked without the turns before them, and are left out of these numbers.

| Without the follow-ups (102), run 1 / 2 | Accuracy | A path missing | A path extra | Model calls |
|---|---|---|---|---|
| keyword rule | 78.4% | 14 | 17 | 0 |
| the 5 nearest questions | 69.6% | 19 | 23 | 0 |
| the model | 84.3% / 83.3% | 3 / 3 | 18 / 18 | 102 |
| the model, both paths below 0.95 (the query path's setting) | | 2 / 2 | 33 / 34 | 102 |
| cascade: the 5 nearest when they all agree | 83.3% / 82.4% | 3 / 3 | 18 / 18 | 75 |

- **The model, on every route**: all 28 data questions and all 12 on both right. Its mistakes are on documents
  questions: 13 sent to both (a path extra) and 3 to data (a path missing).
- **What the instructions had to say.** The first wording lost a path on 7 and 6 questions in two runs, all of them
  documents questions sent to data. Three asked how results compared with their targets: the database has what
  happened, but the targets are only in the campaign plans. One sentence saying so moved all three to both, in both
  runs, and nothing else got worse.
- **What is left is mostly the labels.** Of the 3 questions that still lose a path, two (a product's sales and
  returns, the product with the strongest margin) are answered by the data alone; they are labelled documents because
  they were written from a review. The third asks whether profit already nets the product cost, which the review
  answers. The labels stay as reviewed: changing them after seeing the model's routes would grade the model on its
  own answers.
- **The threshold: 0.95.** Below it both paths run. The model's confidence means something: right on about 60% of its
  routes between 0.7 and 0.9, and 87% from 0.9 up. But the routes that lose a path come at 0.86 to 0.99, so a
  threshold saves little: up to 0.9 it saved no question in either run, at 0.95 the definition question in both.
  The fewest missing paths decide first, so 0.95, at the price of a third of the questions running both paths instead
  of 18%, about $0.0003 for each extra path.
- **The cascade** gave the model's accuracy with a quarter fewer model calls, but the 29 questions it answered without
  the model were nearly all easy documents questions, and it needs the labelled questions kept for the query path.
  Not used.
- **The keyword rule** is the fallback when the model doesn't reply within 2 s: 1 question in each run (7 in the run
  before, a slow hour of the API).
- **Latency: p50 1.1 s, p95 1.6 to 1.7 s a question**, on a development machine; 442 input and 41 output tokens,
  $0.000065 a question. The network is at most 0.2 s of it, the round trip of an embedding request; the rest is the
  model. In front of Text-to-SQL (p50 2.7 s) it would end past the 3 s target, so the query path is designed to route at
  the same time as it embeds the question and searches, not before.
- **Follow-ups**: the 4 on documents went to documents; of the 2 on data, one went to data and one to none in one run,
  both to none in the other. The router sees the question alone until questions are rewritten with the turns before
  them.

## The whole query path: routing, both paths at once, one answer

```bash
RERANKER_URL= uv run python -m evaluation.flow_run --split dev --name 1   # $0.04 a run
```

All 108 questions of `dev` through `app/query_flow.py` as `/query` runs it: the router, the embedding and both paths
started together, the path the route rules out cancelled, then one answer. One question at a time, on a development
machine, without the backend and without the reranker: on a CPU it always gives up after its 0.5 s and keeps the vector
order, so leaving it out only removes that wait. Answers aren't graded here; the SQL is scored on its rows.

| Paths that ran | Questions | Under 3 s | p50 | p95 | Slowest |
|---|---|---|---|---|---|
| documents | 38 | 38 | 1.56 s | 1.99 s | 2.02 s |
| data | 28 | 22 | 2.73 s | 3.21 s | 3.46 s |
| both | 42 | 14 | 3.22 s | 5.22 s | 5.43 s |
| all | 108 | 74 (68.5%) | 2.02 s | 4.54 s | 5.43 s |

- **Questions about documents meet the target.** Their answer is written while the router decides (p50 1.26 s), so
  they end soon after it: p50 1.56 s.
- **Questions about data mostly do**: p50 2.73 s, 22 of 28 under 3 s. The rows are there at p50 1.56 s and the
  explanation takes 1.26 s more. Waiting for the route first was estimated at about 3.8 s.
- **Questions that need both don't.** The 12 of them: p50 4.5 s, none under 3 s. Both paths are done at p50 2.3 s, and
  the answer written from the two takes 2.3 s more: about 150 output tokens, against 33 for an explanation of rows,
  so the 2.9 to 3.1 s estimated from an explanation's time was wrong.
- **Most questions that ran both paths needed one.** 30 of the 42: 21 because the router was below 0.95, 7 because it
  said both, 2 by the keyword rule. 14 of them had no rows and got the documents' answer at once (p50 1.35 s); the
  other 16 got one answer from both and none was under 3 s (p50 3.4 s). The threshold that keeps paths from missing
  costs this much time as well as the extra calls.
- **Routing, as measured on its own:** a path missing on 3 questions (the two labelled documents that the data answers,
  and a follow-up asked without its turns), one extra on 35. The keyword rule decided 5, each after the router's 2 s,
  the first question among them, while its connections were set up; routed alone in its last two runs, 1 each.
- **Text-to-SQL is unchanged by the flow.** Of the 39 questions with a gold result, the rows held it for 30, against 31
  and 30 for the pipeline alone on the same questions; the one difference flips between runs of the pipeline too. Two
  got no rows: a follow-up the router sent to documents, and another follow-up the writer refused.
- **Cost**: $0.039 for the 108, $0.00036 a question, counting only calls that finished. The writer of SQL is the largest
  part, 127,000 input tokens, because a query starts being written for every question. Thrown away: the documents'
  answer of the 28 questions answered from both, 34,000 input and 1,700 output tokens. 28 questions had a path
  cancelled (documents 18, data 10); what a cancelled call had used isn't known.

### With the rewrite

```bash
RERANKER_URL= uv run python -m evaluation.flow_run --split dev --name 2   # $0.048 a run
```

The same 108 questions with the rewrite beside the router (`app/router/rewriting.py`), and each follow-up asked with
the turns before it.

| Paths that ran | Questions | Under 3 s, run 1 / 2 | p50, run 1 / 2 | p95, run 1 / 2 |
|---|---|---|---|---|
| documents | 38 / 38 | 38 / 37 | 1.56 / 1.75 s | 1.99 / 2.86 s |
| data | 28 / 29 | 22 / 10 | 2.73 / 3.14 s | 3.21 / 5.31 s |
| both | 42 / 41 | 14 / 16 | 3.22 / 3.97 s | 5.22 / 7.68 s |

- **11 questions were rewritten**, and they pay for it: p50 5.2 s, the paths started again after the rewrite (p50
  1.26 s). 8 named a campaign by its place in time and opened their answer with it; 3 were a period or a follow-up.
  None was asked back or found nothing: dev has no executive's question about "the last" of a kind of campaign.
- **Waiting for the rewrite slowed no other question**: of the 97 it left as asked, it finished before the paths every
  time. Those questions were slower all the same, because the calls were: the ones beside the rewrite took 9 to 14%
  longer than in run 1 (the router p50 1.26 to 1.41 s, the paths with no restart 1.56 to 1.75 s for documents and 1.56
  to 1.77 s for data). Of the calls that run alone afterwards, the explanation wasn't slower (1.26 to 1.20 s) and the
  answer from both was (1.66 to 1.87 s). Run 3 settles it: with the same four calls, its timings are back to run 1's, so it was a slower hour of the API.
  Data questions sit just under 3 s (p50 2.78 s in run 1, without a restart), so small changes move many across it:
  22 of 28 under it, then 10 of 29.
- **Follow-ups: 2 of 6 rewritten.** "Who signs off on it?" became the inventory check's question, and "And the one
  before that?" found the launch before the one in the previous answer, whose rows held the gold result. Three that
  need their turns came back as they were asked, "And the Beauty one?" among them: the router, which now sees the
  turns, sent it to the data, but the SQL writer got the question alone and its rows were wrong. The rewrite's
  instructions decide this, and dev is where they are tuned.
- **Text-to-SQL**: rows for all 39 questions with a gold result (37 in run 1), the gold result in 32 of them (30). Up:
  the rewritten "the one before that", the rewritten anniversary-sale question, and the question that flips between
  runs of the pipeline alone. Down: one question the rewrite left as asked. The two follow-ups about data both get rows
  now; "And the Beauty one?" wrong ones.
- **Routing**: a path missing on 2 questions (3 in run 1): with the turns, "And the Beauty one?" went to the data.
- **Cost**: $0.048, $0.0004 a question; the rewrite $0.009 of it, mostly its instructions as input.

### Run 3: with the tuned rewrite

```bash
RERANKER_URL= uv run python -m evaluation.flow_run --split dev --name 3   # $0.054 a run
```

| Paths that ran | Questions | Under 3 s, run 1 / 2 / 3 | p50, run 1 / 2 / 3 | p95, run 1 / 2 / 3 |
|---|---|---|---|---|
| documents | 38 / 38 / 37 | 38 / 37 / 36 | 1.56 / 1.75 / 1.60 s | 1.99 / 2.86 / 2.74 s |
| data | 28 / 29 / 29 | 22 / 10 / 17 | 2.73 / 3.14 / 2.76 s | 3.21 / 5.31 / 4.55 s |
| both | 42 / 41 / 42 | 14 / 16 / 17 | 3.22 / 3.97 / 3.33 s | 5.22 / 7.68 / 5.20 s |

- **Run 2's slowness was the API, not the fourth call at once.** Run 3 has the same four calls while the router decides,
  and the questions left as asked are back where run 1 was: documents p50 1.54 s (1.56 in run 1), data 2.66 s (2.78),
  17 of 22 data questions under 3 s (22 of 28), the router 1.23 s (1.26).
- **14 questions rewritten**, 5 of the 6 follow-ups among them, and they pay for it: p50 3.7 s, 3 under 3 s. Of the 92
  others, only "the home black friday live", taken as the latest, which is the only one.
- **Text-to-SQL**: the gold result in 33 of 39 (30 in run 1, 32 in run 2). "And the Beauty one?", written out now,
  gets the right rows; one rewritten question about the anniversary sale went wrong this time after going right in run 2.
- **Cost**: $0.054, $0.0005 a question; the rewrite $0.014 of it (run 2: $0.009), because the examples made its
  instructions longer.

### Tuning the rewrite's instructions

```bash
uv run python -m evaluation.rewrite_run --split dev --name hard-5 --only hard   # the rewrite alone, $0.0025
```

The rewrite alone (`evaluation/rewrite_run.py`), on the 6 follow-ups and the 10 questions with a word like "last" or
"this year", and once on all 108; no search, no SQL, no answer. The questions have no label for the rewrite, so it is
read by eye: a follow-up should come back written out, a campaign by its place in time with `{campaign}`, and
everything else as asked. "Wrong" is a reference the question didn't make.

| Instructions | Runs | Follow-ups written out (6) | Relative words right (10) | Wrong |
|---|---|---|---|---|
| first, as in run 2 above | `dev-flow-2` | 2 | 8 | 0 |
| + what "stands on its own" means, examples | `hard-1` | 6 | 7 | 1: "the previous batch" became a campaign |
| + two jobs apart, what isn't a campaign | `hard-2` | 4 | 8 | 0 |
| + a follow-up example with "the one before it" | `hard-3`, `hard-4` | 5, 5 | 8, 7 (one past its 2 s) | 0, 0 |
| + a campaign named by its date stays as asked (now in the code) | `hard-5` | 5 | 8 | 0 |

- **Follow-ups**: the first instructions sent "And the Beauty one?" and two more back as they were asked; a definition of
  what stands on its own, and two examples from other companies, fixed them. The one left as asked, "who updates the
  stream plan if a shade gets pulled?", can be read without its turns.
- **Saying more broke other things.** With the definition alone, "the last one" in a question about the anniversary
  sale and "last Beauty Black Friday" came back as standing on their own, and "the previous batch" became a campaign:
  the rewrite then had to be told its two jobs apart, and that batches, tests and steps aren't campaigns.
- **On all 108** (`all-1`, the instructions before the last row): 2 of the 92 other questions were rewritten. "The
  November 2025 Grocery Fan Appreciation Day" became the latest one, which would have answered about another; the last
  row's rule fixed it (`hard-5`). "The home black friday live" is still taken as the latest, which here is the only
  one. With the last row's instructions, in run 3 below, it is the only one of the 92 rewritten.
- **Cost and time**: $0.024 for the six runs; the examples make the instructions about 800 tokens, so a question's
  rewrite costs about $0.00013. p50 about 1.0 s, p95 1.5 to 2.0 s: 1 of 16 went past the 2 s limit once.

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
| `dev-sql-pipeline-broad-refusal` | the pipeline's, broad NOT_IN_DATA rule | rejected |
| `dev-sql-pipeline-earlier-layout-1` | the pipeline's, question last | rejected; see "What the prompt's wording did" |
| `dev-sql-pipeline-earlier-layout-2` | the pipeline's, question last | rejected; the reply that went on with the pattern |
| `dev-sql-pipeline-1` | now in the code | Text-to-SQL pipeline |
| `dev-sql-pipeline-2` | now in the code | Text-to-SQL pipeline; noise |
| `dev-sql-pipeline-reader` | now in the code | Text-to-SQL pipeline as `sql_reader`; the ablation's full pipeline, run 1 |
| `dev-sql-ablation-<configuration>-<1, 2>` | now in the code | the ablation's other 15 runs ([dev-sql-ablation.md](../eval/results/dev-sql-ablation.md)) |
| `sql-leak-1`, `sql-leak-2` | before the rule for another department | questions that try another department's data |
| `sql-leak-dev-before` | before the rule for another department | the dev questions the rule was worked out on |
| `sql-leak-dev-1`, `sql-leak-dev-2` | now in the code | the same, with the rule |
| `sql-leak-3`, `sql-leak-4` | now in the code | the check questions, with the rule |
| `dev-sql-pipeline-department-rule-1`, `-2` | now in the code | Text-to-SQL pipeline with the rule: the regression |
| `dev-route-1`, `dev-route-2` | the router's first instructions | routing, before the sentence on targets |
| `dev-route-3`, `dev-route-4` | now in the code | routing |
| `dev-flow-1` | before the rewrite | the whole query path, without the reranker |
| `dev-flow-2` | the rewrite's first instructions | the same, with the rewrite and each follow-up's turns |
| `dev-rewrite-hard-1` to `-hard-4`, `-all-1` | earlier rewrite instructions | the rewrite alone, while tuning it |
| `dev-rewrite-hard-5` | now in the code | the rewrite alone |
| `dev-flow-3` | now in the code | the whole query path, with the tuned rewrite |

Each run is a `.jsonl` of what every question went through, an `.md` of its tables with the instructions it used, and a
spot-check file of 30 graded answers picked at random, except the SQL runs, which need no grader. The seven document
runs cost about $1.98, $1.87 of it grading; the two SQL baseline runs $0.08, and the two they replace $0.06; the five
pipeline runs $0.05, the run as `sql_reader` $0.011, the ablation's 15 runs $0.16, the two leak checks $0.012, and the rule for another department's questions, worked
out and checked, $0.041; the four routing runs $0.027; the three runs of the whole query path $0.141; tuning the rewrite $0.024.
