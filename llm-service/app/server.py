"""Starts the API: `python -m app.server`, with `--reload` while developing.

Why not `uvicorn app.main:app`: async psycopg can't run on ProactorEventLoop, the default on Windows, and uvicorn
only switches away from it when reloading. Measured: with `--reload` the database connects, without it every query
fails, a difference that would hide until the first run without reload. So the event loop is chosen here, before
uvicorn starts. Linux doesn't have this problem, but going through this entry point there changes nothing.

It listens on 127.0.0.1 unless LLM_SERVICE_HOST says otherwise: this service trusts the role in the request, so it
must only be reachable by the backend (docs/permission-model.md).

One process reaches about 100 QPS, because past that the limit is one CPU core (measured, see llm-service/README.md), so
production runs several with --workers or LLM_SERVICE_WORKERS. They share the port and the kernel hands out connections.
"""
import argparse
import asyncio
import sys

import uvicorn

from app.config import settings


# Through uvicorn, which applies it in every process. logging.basicConfig here would reach only the parent: measured with
# 2 workers, not one query was logged, and the query log is where its tokens and timings are recorded
LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)-7s %(name)s: %(message)s"}},
    "handlers": {"stderr": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"level": "INFO", "handlers": ["stderr"]},
}


# How long an idle connection stays open. Above the 30 s for which the backend's HTTP client keeps one: with uvicorn's
# default of 5 s, a query sent just as the server closed the connection got nothing back, and a 503. Measured with bursts
# 4.95 s apart: 1 of 1,200, and none of 800 with bursts 2 s apart
KEEP_ALIVE_SECONDS = 35


def choose_event_loop() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reload", action="store_true", help="restart when the code changes")
    parser.add_argument("--port", type=int, default=settings.llm_service_port)
    parser.add_argument("--workers", type=int, default=settings.llm_service_workers, help="processes sharing the port")
    args = parser.parse_args()

    choose_event_loop()
    uvicorn.run("app.main:app", host=settings.llm_service_host, port=args.port, reload=args.reload, workers=args.workers,
                log_config=LOG_CONFIG, timeout_keep_alive=KEEP_ALIVE_SECONDS)


if __name__ == "__main__":
    main()
