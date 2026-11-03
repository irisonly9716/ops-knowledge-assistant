"""Starts the API: `python -m app.server`, with `--reload` while developing.

Why not `uvicorn app.main:app`: async psycopg can't run on ProactorEventLoop, the default on Windows, and uvicorn
only switches away from it when reloading. Measured: with `--reload` the database connects, without it every query
fails, a difference that would hide until the first run without reload. So the event loop is chosen here, before
uvicorn starts. Linux doesn't have this problem, but going through this entry point there changes nothing.

It listens on 127.0.0.1 unless LLM_SERVICE_HOST says otherwise: this service trusts the role in the request, so it
must only be reachable by the backend (docs/permission-model.md).
"""
import argparse
import asyncio
import logging
import sys

import uvicorn

from app.config import settings


def choose_event_loop() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reload", action="store_true", help="restart when the code changes")
    parser.add_argument("--port", type=int, default=settings.llm_service_port)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    choose_event_loop()
    uvicorn.run("app.main:app", host=settings.llm_service_host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
