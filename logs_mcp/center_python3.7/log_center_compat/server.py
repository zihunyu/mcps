"""Executable server for the Python 3.7 compatible Log Center."""

import logging

from .app import create_app
from .config import load_config


def main(argv=None):
    settings = load_config()
    logging.basicConfig(
        level=getattr(logging, settings.server.log_level.upper()),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app = create_app(settings)
    app.run(
        host=settings.server.host,
        port=settings.server.port,
        debug=False,
        threaded=True,
    )


if __name__ == "__main__":
    main()
