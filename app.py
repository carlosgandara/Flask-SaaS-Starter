# /app.py

import os

from flask import Flask

from auth import init_auth
from billing import init_billing
from configuration import configure_app
from diagnostics import init_diagnostics


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "false").lower() == "true",
        RATELIMIT_STORAGE_URI=os.getenv("RATELIMIT_STORAGE_URI", "memory://"),
    )
    if config:
        app.config.update(config)
    configure_app(app)
    init_diagnostics(app)
    init_auth(app)
    init_billing(app)
    from database_migrations import init_migrations
    init_migrations(app)
    return app

app = create_app()


if __name__ == "__main__":
    app.run()
