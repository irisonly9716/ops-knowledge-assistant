"""The Text-to-SQL ablation: what each part of the fixed pipeline is worth, read from the result files of
evaluation.sql_run. No model calls here; each configuration is run twice with sql_run first:

    uv run python -m evaluation.sql_run --split dev --name ablation-<configuration>-<1|2> <its flags, below>
    uv run python -m evaluation.sql_ablation --split dev

Two readings, because either alone misleads:
  - adding one part at a time, in the order the pipeline is built: zero-shot on the tables, the data dictionary, the
    views, 3 examples, 2 retries. What a part seems to add depends on what came before it.
  - taking one part out of the full pipeline: what it is worth with all the others there. A part that does work
    another part also does shows little here and more above.

The model doesn't answer the same way twice, so every configuration runs twice and both runs are shown. With 27 data
questions one question is 3.7 points. A step is credited with a question only when the question is wrong in both runs
before it and right in both runs after (and the reverse for one it breaks); the rest is the model's noise.

Writes eval/results/<split>-sql-ablation.md.
"""
import argparse
import json
from dataclasses import dataclass
from decimal import Decimal

from evaluation import metrics
from evaluation.run import RESULTS_DIR, pct


@dataclass(frozen=True)
class Configuration:
    label: str
    flags: str
    runs: tuple[str, ...]   # the names given to sql_run


def configuration(key: str, label: str, flags: str, runs: tuple[str, ...] = ()) -> Configuration:
    return Configuration(label, flags, runs or (f"ablation-{key}-1", f"ablation-{key}-2"))


FULL_FLAGS = "--pipeline --dictionary --views --examples 3 --retries 2"
ADDING = (
    configuration("zero-shot", "zero-shot, on the tables", "--pipeline"),
    configuration("dictionary", "+ the data dictionary and the names in the data", "--pipeline --dictionary"),
    configuration("views", "+ the metric views instead of the tables", "--pipeline --dictionary --views"),
    configuration("examples", "+ the 3 closest example questions", "--pipeline --dictionary --views --examples 3"),
    # Its first run is the one made as sql_reader on the same code
    configuration("full", "+ 2 retries with the error: the query path", FULL_FLAGS,
                  ("pipeline-reader", "ablation-full-2")),
)
FULL = ADDING[-1]
TAKING_OUT = (
    configuration("without-dictionary", "without the data dictionary", "--pipeline --views --examples 3 --retries 2"),
    configuration("without-views", "without the views (the tables)", "--pipeline --dictionary --examples 3 --retries 2"),
    configuration("without-examples", "without the examples", "--pipeline --dictionary --views --retries 2"),
    Configuration("without the retries", ADDING[3].flags, ADDING[3].runs),   # the step before the full pipeline
)


def load(split: str, name: str) -> dict[str, dict] | None:
    path = RESULTS_DIR / f"{split}-sql-{name}.jsonl"
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        return {r["id"]: r for r in (json.loads(line) for line in f)}


@dataclass(frozen=True)
class Measured:
    runs: list[dict[str, dict]]

    def accuracy(self, route: str, key: str = "exact") -> list[float]:
        return [metrics.mean(float(r[key]) for r in run.values() if r["route"] == route) for run in self.runs]

    def mean_accuracy(self, route: str) -> float:
        return metrics.mean(self.accuracy(route))

    def records(self) -> list[dict]:
        return [r for run in self.runs for r in run.values()]

    def stable(self, question_id: str) -> bool | None:
        """Right in every run, wrong in every run, or None when the runs disagree."""
        results = {run[question_id]["exact"] for run in self.runs}
        return results.pop() if len(results) == 1 else None

    def summary(self) -> str:
        records = self.records()
        totals = [r["total_ms"] for r in records if r["status"] != "error"]
        cost = sum((Decimal(r["cost_usd"]) for r in records), Decimal(0)) / len(records)
        failed = sum(1 for r in records for q in r["queries"] if q["error"]) / len(self.runs)
        return (f"{metrics.percentile(totals, 50) / 1000:.1f} s | {metrics.mean(r['model_calls'] for r in records):.1f} | "
                f"{failed:.1f} | ${cost:.5f}")


def measure(split: str, c: Configuration) -> Measured | None:
    runs = [load(split, name) for name in c.runs]
    return None if any(run is None for run in runs) else Measured(runs)


def changed(before: Measured, after: Measured, route: str = "data") -> tuple[list[dict], list[dict]]:
    """The questions a step fixed and broke, counting only those both runs agree on, on either side."""
    fixed, broke = [], []
    for qid, record in after.runs[0].items():
        if record["route"] != route:
            continue
        was, now = before.stable(qid), after.stable(qid)
        if was is False and now is True:
            fixed.append(record)
        elif was is True and now is False:
            broke.append(record)
    return fixed, broke


def runs_cell(m: Measured, route: str, key: str = "exact") -> str:
    return " / ".join(pct(v) for v in m.accuracy(route, key))


def summarize(split: str) -> str:
    adding = [(c, measure(split, c)) for c in ADDING]
    taking_out = [(c, measure(split, c)) for c in TAKING_OUT]
    full = adding[-1][1]
    missing = list(dict.fromkeys(name for c, m in adding + taking_out if m is None for name in c.runs
                                 if load(split, name) is None))
    any_run = next(run for c in ADDING + TAKING_OUT for run in map(lambda name: load(split, name), c.runs) if run)
    n_data = sum(1 for r in any_run.values() if r["route"] == "data")
    lines = [f"# Text-to-SQL ablation on {split}", "",
             f"Each configuration of the fixed pipeline, run twice on the {split} questions with a gold SQL "
             f"({n_data} data questions, where the number is the answer, and the questions on both documents and "
             "data). Execution accuracy as in `evaluation.sql_run`: the last query's rows hold the gold rows' numbers. "
             f"One data question is {100 / n_data:.1f} points.", ""]
    if missing:
        lines += [f"Not run yet: {', '.join(missing)}.", ""]

    lines += ["## Adding one part at a time", "",
              "| Configuration | Data, run 1 / 2 | Strict | Both | p50 | Model calls | Failed queries a run | Cost a "
              "question | Fixed / broke |", "|---|---|---|---|---|---|---|---|---|"]
    previous = None
    for c, m in adding:
        if m is None:
            lines.append(f"| {c.label} | not run | | | | | | | |")
            previous = None
            continue
        flips = "–"
        if previous is not None:
            fixed, broke = changed(previous, m)
            flips = f"{len(fixed)} / {len(broke)}"
        lines.append(f"| {c.label} | {runs_cell(m, 'data')} | {runs_cell(m, 'data', 'exact_every_value')} | "
                     f"{runs_cell(m, 'both')} | {m.summary()} | {flips} |")
        previous = m

    lines += ["", "## Taking one part out of the full pipeline", "",
              "| Configuration | Data, run 1 / 2 | Against the full pipeline | Both | p50 | Model calls | Failed "
              "queries a run | Cost a question | Lost / gained |", "|---|---|---|---|---|---|---|---|---|"]
    if full is not None:
        lines.append(f"| the full pipeline | {runs_cell(full, 'data')} | – | {runs_cell(full, 'both')} | "
                     f"{full.summary()} | – |")
    for c, m in taking_out:
        if m is None or full is None:
            lines.append(f"| {c.label} | not run | | | | | | | |")
            continue
        lost, gained = changed(m, full)   # what the part fixes is what taking it out loses
        delta = (m.mean_accuracy("data") - full.mean_accuracy("data")) * 100
        lines.append(f"| {c.label} | {runs_cell(m, 'data')} | {delta:+.1f} points | {runs_cell(m, 'both')} | "
                     f"{m.summary()} | {len(lost)} / {len(gained)} |")

    lines += ["", "## The questions behind each change", "",
              "Data questions both runs agree on, on both sides of the change.", ""]
    previous = None
    for c, m in adding:
        if m is not None and previous is not None:
            fixed, broke = changed(previous, m)
            lines += [f"**{c.label}**: fixed {len(fixed)}, broke {len(broke)}", ""]
            lines += [f"- fixed `{r['id']}` {r['question']}" for r in fixed]
            lines += [f"- broke `{r['id']}` {r['question']}" for r in broke]
            lines.append("")
        previous = m
    for c, m in taking_out:
        if m is not None and full is not None:
            lost, gained = changed(m, full)
            lines += [f"**{c.label}**: loses {len(lost)}, gains {len(gained)}", ""]
            lines += [f"- loses `{r['id']}` {r['question']}" for r in lost]
            lines += [f"- gains `{r['id']}` {r['question']}" for r in gained]
            lines.append("")

    lines += ["## How each was run", "", "| Configuration | sql_run flags | Result files |", "|---|---|---|"]
    lines += [f"| {c.label} | `{c.flags}` | {', '.join(f'`{split}-sql-{name}`' for name in c.runs)} |"
              for c in ADDING + TAKING_OUT[:3]]
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    args = parser.parse_args()
    path = RESULTS_DIR / f"{args.split}-sql-ablation.md"
    path.write_text(summarize(args.split), encoding="utf-8", newline="\n")
    print(f"written: {path}")


if __name__ == "__main__":
    main()
