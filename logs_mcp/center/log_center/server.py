"""Log Center executable entrypoint."""

from __future__ import annotations

import logging

import uvicorn

from .app import create_app
from .config import load_config


def main() -> None:
    settings = load_config()
    logging.basicConfig(
        level=settings.server.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app = create_app(settings)
    uvicorn.run(
        app,
        host=settings.server.host,
        port=settings.server.port,
        log_level=settings.server.log_level,
    )


if __name__ == "__main__":
    main()
