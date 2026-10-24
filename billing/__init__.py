"""Optional billing blueprint. No remote calls or schema changes during setup."""

import click

from .config import load_config


def init_billing(app):
    app.config["MAX_CONTENT_LENGTH"] = min(app.config.get("MAX_CONTENT_LENGTH") or 262144, 262144)
    load_config(app)
    from .providers import build_providers
    from .routes import billing_bp

    app.extensions["billing_providers"] = build_providers(app.config) if app.config["BILLING_ENABLED"] else {}
    app.register_blueprint(billing_bp)
    from .security import configure_overlay_policy
    configure_overlay_policy(app)

    @app.cli.command("billing-init-db")
    def init_db():
        """Create the initial billing tables. Run once before enabling checkout."""
        from auth.db import Base, engine
        from .models import BILLING_TABLES
        Base.metadata.create_all(bind=engine, tables=BILLING_TABLES)
        click.echo("Billing tables created (existing data preserved).")

    @app.cli.command("billing-reconcile")
    def reconcile():
        """Refresh local subscriptions from provider state after missed webhooks."""
        import secrets
        from sqlalchemy import select
        from auth.db import SessionLocal
        from .models import Subscription
        from .providers.base import Event
        from .service import process_event
        if not app.config["BILLING_ENABLED"]:
            raise click.ClickException("Enable billing first")
        # Keyset pagination keeps memory bounded as the subscription table grows.
        last_id, count, failures = 0, 0, 0
        while True:
            with SessionLocal() as db:
                rows = db.execute(select(Subscription).where(
                    Subscription.id > last_id, Subscription.mode == app.config["BILLING_MODE"])
                    .order_by(Subscription.id).limit(100)).scalars().all()
            if not rows:
                break
            for row in rows:
                last_id = row.id
                try:
                    process_event(row.provider, Event("reconcile_" + secrets.token_hex(16),
                                                      "reconcile", row.external_id))
                    count += 1
                except Exception as exc:
                    failures += 1
                    app.logger.error("billing.reconcile_failed subscription=%s type=%s", row.id, type(exc).__name__)
        click.echo(f"Reconciled {count}; failed {failures}.")
        if failures:
            raise click.ClickException("Some subscriptions could not be reconciled; check billing logs")
