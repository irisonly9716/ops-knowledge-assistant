# Text-to-SQL ablation on dev

Each configuration of the fixed pipeline, run twice on the dev questions with a gold SQL (27 data questions, where the number is the answer, and the questions on both documents and data). Execution accuracy as in `evaluation.sql_run`: the last query's rows hold the gold rows' numbers. One data question is 3.7 points.

## Adding one part at a time

| Configuration | Data, run 1 / 2 | Strict | Both | p50 | Model calls | Failed queries a run | Cost a question | Fixed / broke |
|---|---|---|---|---|---|---|---|---|
| zero-shot, on the tables | 51.9% / 51.9% | 48.1% / 44.4% | 25.0% / 25.0% | 3.1 s | 1.8 | 0.0 | $0.00020 | – |
| + the data dictionary and the names in the data | 70.4% / 70.4% | 55.6% / 55.6% | 50.0% / 25.0% | 2.8 s | 1.9 | 0.5 | $0.00031 | 6 / 0 |
| + the metric views instead of the tables | 88.9% / 85.2% | 63.0% / 59.3% | 75.0% / 66.7% | 2.8 s | 1.9 | 1.0 | $0.00026 | 4 / 0 |
| + the 3 closest example questions | 88.9% / 88.9% | 66.7% / 66.7% | 66.7% / 75.0% | 2.7 s | 1.9 | 0.0 | $0.00028 | 0 / 0 |
| + 2 retries with the error: the query path | 85.2% / 85.2% | 63.0% / 63.0% | 58.3% / 58.3% | 2.7 s | 1.9 | 0.5 | $0.00028 | 0 / 1 |

## Taking one part out of the full pipeline

| Configuration | Data, run 1 / 2 | Against the full pipeline | Both | p50 | Model calls | Failed queries a run | Cost a question | Lost / gained |
|---|---|---|---|---|---|---|---|---|
| the full pipeline | 85.2% / 85.2% | – | 58.3% / 58.3% | 2.7 s | 1.9 | 0.5 | $0.00028 | – |
| without the data dictionary | 74.1% / 70.4% | -13.0 points | 25.0% / 25.0% | 2.4 s | 1.8 | 0.0 | $0.00018 | 2 / 0 |
| without the views (the tables) | 70.4% / 74.1% | -13.0 points | 41.7% / 41.7% | 3.4 s | 2.0 | 3.5 | $0.00034 | 3 / 0 |
| without the examples | 85.2% / 88.9% | +1.9 points | 66.7% / 66.7% | 2.3 s | 2.0 | 1.0 | $0.00027 | 0 / 1 |
| without the retries | 88.9% / 88.9% | +3.7 points | 66.7% / 75.0% | 2.7 s | 1.9 | 0.0 | $0.00028 | 0 / 1 |

## The questions behind each change

Data questions both runs agree on, on both sides of the change.

**+ the data dictionary and the names in the data**: fixed 6, broke 0

- fixed `q-cd428e41fe` How much GMV did last Black Friday's electronics livestream bring in?
- fixed `q-123c6ee65c` What was the conversion rate for our most recent product launch?
- fixed `q-2d8f4b1df2` What was the peak live audience for our summer electronics sale?
- fixed `q-b66ecb8a0e` What was the average conversion rate for last Black Friday's grocery live?
- fixed `q-42f95e9ad2` Which product brought in the most sales revenue during the 2026 Grocery Summer Sale, and how many units did it sell?
- fixed `q-faeada22c8` which region spent the most on our back-to-school grocery live, and how much?

**+ the metric views instead of the tables**: fixed 4, broke 0

- fixed `q-91b4968461` Which finished electronics campaign had our worst ROI, and what was it?
- fixed `q-db8a36c5f0` What was the ROI for the September 2026 grocery anniversary campaign?
- fixed `q-2c510597be` For the Beauty New Product Launch, what was the ROI and actual operating cost?
- fixed `q-1f737aa58d` Which finished campaign that started in June 2026 had the highest ROI?

**+ the 3 closest example questions**: fixed 0, broke 0


**+ 2 retries with the error: the query path**: fixed 0, broke 1

- broke `q-b66ecb8a0e` What was the average conversion rate for last Black Friday's grocery live?

**without the data dictionary**: loses 2, gains 0

- loses `q-2d8f4b1df2` What was the peak live audience for our summer electronics sale?
- loses `q-faeada22c8` which region spent the most on our back-to-school grocery live, and how much?

**without the views (the tables)**: loses 3, gains 0

- loses `q-db8a36c5f0` What was the ROI for the September 2026 grocery anniversary campaign?
- loses `q-2c510597be` For the Beauty New Product Launch, what was the ROI and actual operating cost?
- loses `q-2abe10703f` What was the total GMV for campaigns that started in September 2026?

**without the examples**: loses 0, gains 1

- gains `q-b66ecb8a0e` What was the average conversion rate for last Black Friday's grocery live?

**without the retries**: loses 0, gains 1

- gains `q-b66ecb8a0e` What was the average conversion rate for last Black Friday's grocery live?

## How each was run

| Configuration | sql_run flags | Result files |
|---|---|---|
| zero-shot, on the tables | `--pipeline` | `dev-sql-ablation-zero-shot-1`, `dev-sql-ablation-zero-shot-2` |
| + the data dictionary and the names in the data | `--pipeline --dictionary` | `dev-sql-ablation-dictionary-1`, `dev-sql-ablation-dictionary-2` |
| + the metric views instead of the tables | `--pipeline --dictionary --views` | `dev-sql-ablation-views-1`, `dev-sql-ablation-views-2` |
| + the 3 closest example questions | `--pipeline --dictionary --views --examples 3` | `dev-sql-ablation-examples-1`, `dev-sql-ablation-examples-2` |
| + 2 retries with the error: the query path | `--pipeline --dictionary --views --examples 3 --retries 2` | `dev-sql-pipeline-reader`, `dev-sql-ablation-full-2` |
| without the data dictionary | `--pipeline --views --examples 3 --retries 2` | `dev-sql-ablation-without-dictionary-1`, `dev-sql-ablation-without-dictionary-2` |
| without the views (the tables) | `--pipeline --dictionary --examples 3 --retries 2` | `dev-sql-ablation-without-views-1`, `dev-sql-ablation-without-views-2` |
| without the examples | `--pipeline --dictionary --views --retries 2` | `dev-sql-ablation-without-examples-1`, `dev-sql-ablation-without-examples-2` |
