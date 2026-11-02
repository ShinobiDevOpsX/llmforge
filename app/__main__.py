"""Container entrypoint: ``python -m app``."""

import uvicorn

from app.config import get_settings
from app.main import create_app


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_config=None,  # logging is configured by create_app()
        access_log=False,  # access logs come from RequestContextMiddleware
        proxy_headers=False,
        server_header=False,
        timeout_graceful_shutdown=30,
    )


if __name__ == "__main__":
    main()
