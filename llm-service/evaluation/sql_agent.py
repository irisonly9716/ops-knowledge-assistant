"""The Text-to-SQL baseline: LangChain's agent (create_agent, on LangGraph) with tools to look at the database and
run queries, the way LangChain's SQL agent works.

LangChain's own SQL tools are in langchain-community, which LangChain stopped maintaining in May 2026 and which needs
a newer pydantic-settings than this service pins. Its schema tool also shows the model three rows of every table, IP
addresses included. The three tools here do the same jobs (list the tables, show their columns, run a query) through
this service's read-only connections, and show no rows but a query's own result.

Zero-shot: the model is told who is asking and what today's date is, and finds the rest with its tools. dictionary
adds the comments of migration V9 and the names found in the data; views offers the metric views instead of the
tables. Those two are what the comparison with the fixed pipeline adds one at a time.

Every wait is bounded: a model call has a timeout and no retries, the agent stops after MAX_MODEL_CALLS, and a
query has 5 s. One model call can ask for several queries, so the bound on the whole is the caller's time limit for a
question (evaluation/sql_run.py), not a count.
"""
import time
from dataclasses import dataclass, field
from datetime import date

import psycopg
from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain_core.callbacks import BaseCallbackHandler, UsageMetadataCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import tool
from psycopg_pool import AsyncConnectionPool

from app.sql_agent import schema
from app.sql_agent.execute import QueryResult, SqlRejected, run_select

MAX_MODEL_CALLS = 8
MODEL_TIMEOUT_SECONDS = 30.0
ROWS_SHOWN = 50   # of a result, to the model; the run scores all of them
# Graph steps, a backstop: with the call limit's hooks a model call and its tools take 4 steps, so the call limit stops
# a run first
RECURSION_LIMIT = 6 * MAX_MODEL_CALLS


@dataclass
class Executed:
    sql: str
    ms: float = 0.0
    result: QueryResult | None = None
    error: str | None = None


@dataclass
class AgentRun:
    """What one question went through. The answer to score is the last query that ran: the one the reply is from."""
    queries: list[Executed] = field(default_factory=list)
    reply: str | None = None
    stopped: bool = False   # it wanted more model calls than MAX_MODEL_CALLS
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def final(self) -> Executed | None:
        return next((q for q in reversed(self.queries) if q.result is not None), None)


def instructions(role: str, department: str | None, today: date) -> str:
    asker = schema.visible_department(role, department)
    who = (f"The question comes from a {role} in the {asker} department, who may only see that department's data: "
           f"every query must keep to the {asker} department's campaigns."
           if asker else f"The question comes from an {role}, who may see every department's data.")
    return f"""You answer questions about an operations team's livestream campaigns by querying its PostgreSQL database.

{who} Today is {today.isoformat()}.

First list the tables, then read the columns of the ones that look relevant. Then write one PostgreSQL SELECT query \
that answers the question and run it. If it fails, read the error, fix the query and run it again. Only ever write \
SELECT queries.

Return every row the question asks for; add a LIMIT only when the question asks for a number of them. When the \
query has run, answer the question in a sentence or two from its result."""


def _show(result: QueryResult) -> str:
    lines = [" | ".join(result.columns)]
    lines += [" | ".join("NULL" if v is None else v for v in row) for row in result.rows[:ROWS_SHOWN]]
    hidden = len(result.rows) - ROWS_SHOWN
    if hidden > 0 or result.truncated:
        lines.append(f"({'more than ' if result.truncated else ''}{max(hidden, 0)} more rows not shown)")
    return "\n".join(lines) if result.rows else "(no rows)"


def make_tools(pool: AsyncConnectionPool, run: AgentRun, *, role: str, department: str | None,
               dictionary: bool, views: bool):
    names = schema.METRIC_VIEWS if views else schema.BASE_TABLES

    @tool
    async def list_tables() -> str:
        """List the tables you can query."""
        return ", ".join(names)

    @tool
    async def describe_tables(table_names: str) -> str:
        """Show the columns of some tables. Input: their names, separated by commas."""
        asked = [n.strip() for n in table_names.split(",") if n.strip()]
        unknown = [n for n in asked if n not in names]
        if unknown or not asked:
            return f"Error: no such table: {', '.join(unknown) or '(none given)'}. The tables are: {', '.join(names)}"
        async with pool.connection() as conn:
            text = schema.describe(await schema.relations(conn, asked), comments=dictionary)
            if dictionary:
                text += "\n\n" + schema.describe_values(await schema.known_values(conn, role, department))
        return text

    @tool
    async def run_query(sql: str) -> str:
        """Run one PostgreSQL SELECT query and see its result, or the error if it fails."""
        started = time.perf_counter()
        executed = Executed(sql)
        run.queries.append(executed)
        try:
            async with pool.connection() as conn:
                executed.result = await run_select(conn, sql)
        except (SqlRejected, psycopg.Error) as error:
            executed.error = f"{type(error).__name__}: {error}"[:500]
        executed.ms = (time.perf_counter() - started) * 1000
        return f"Error: {executed.error}" if executed.error else _show(executed.result)

    return [list_tables, describe_tables, run_query]


class _ModelCalls(BaseCallbackHandler):
    """Counts the model calls of a run, including those of a run that was stopped or cancelled."""
    run_inline = True

    def __init__(self) -> None:
        self.calls = 0

    def on_llm_end(self, response, **kwargs) -> None:
        self.calls += 1


async def ask(model: BaseChatModel, pool: AsyncConnectionPool, question: str, *, role: str, department: str | None,
              today: date, dictionary: bool = False, views: bool = False, run: AgentRun | None = None) -> AgentRun:
    """One question. Pass run to keep what happened when the caller's time limit cancels this."""
    run = run if run is not None else AgentRun()
    agent = create_agent(model, make_tools(pool, run, role=role, department=department, dictionary=dictionary,
                                           views=views),
                         system_prompt=instructions(role, department, today),
                         middleware=[ModelCallLimitMiddleware(run_limit=MAX_MODEL_CALLS, exit_behavior="error")])
    usage, calls = UsageMetadataCallbackHandler(), _ModelCalls()
    try:
        result = await agent.ainvoke({"messages": [{"role": "user", "content": question}]},
                                     config={"callbacks": [usage, calls], "recursion_limit": RECURSION_LIMIT})
        last = result["messages"][-1]
        if last.type == "ai" and not last.tool_calls:
            run.reply = last.text
    except ModelCallLimitExceededError:
        run.stopped = True   # it wanted another model call after the last one allowed: no reply
    finally:
        run.model_calls = calls.calls
        run.input_tokens = sum(u.get("input_tokens", 0) for u in usage.usage_metadata.values())
        run.output_tokens = sum(u.get("output_tokens", 0) for u in usage.usage_metadata.values())
    return run
