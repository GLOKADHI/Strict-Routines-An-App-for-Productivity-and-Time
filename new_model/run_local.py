"""Start either local analysis API with loopback binding enforced."""
from __future__ import annotations

import argparse

import uvicorn

from local_api_security import validate_bind_host


LOCAL_HOST = "127.0.0.1"
APPLICATIONS = {
    "legacy": "app.main:app",
    "evidence": "evidence_pipeline.api:app",
}


def server_config(application: str, *, host: str = LOCAL_HOST, port: int | None = None) -> dict:
    if application not in APPLICATIONS:
        raise ValueError("Unknown local analysis application.")
    host = validate_bind_host(host)
    selected_port = (8001 if application == "evidence" else 8000) if port is None else port
    if isinstance(selected_port, bool) or not isinstance(selected_port, int) or not 1 <= selected_port <= 65535:
        raise ValueError("Port must be between 1 and 65535.")
    return {"app": APPLICATIONS[application], "host": host, "port": selected_port}


def create_parser():
    parser = argparse.ArgumentParser(description="Run a StrictRecords local analysis service.")
    parser.add_argument("application", choices=sorted(APPLICATIONS))
    parser.add_argument("--port", type=int)
    parser.add_argument("--reload", action="store_true")
    return parser


def main(argv=None):
    args = create_parser().parse_args(argv)
    config = server_config(args.application, port=args.port)
    uvicorn.run(config["app"], host=config["host"], port=config["port"], reload=args.reload)


if __name__ == "__main__":
    main()
