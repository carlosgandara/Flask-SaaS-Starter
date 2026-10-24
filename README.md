# Flask authentication with optional subscription billing

A Flask application with email verification, password reset, bcrypt password
hashing, HttpOnly JWT cookies, refresh-token rotation, and optional hosted
subscription checkout through Stripe or Lemon Squeezy.

## Project layout

```text
app.py                         Application creation and module registration
auth/
    __init__.py                Auth blueprint, limiter, security headers
    routes.py                  Authentication pages and API
    config.py                  Authentication environment settings
    db.py                      Shared database engine and auth tables
    mail_service.py            SMTP email
    static/css/app.css
    static/js/app.js
    templates/auth/            Authentication HTML pages
billing/
    config.py                  Optional billing configuration and plan catalog
    routes.py                  Checkout, portal, status, signed webhooks
    service.py                 Transactional subscription rules
    models.py                  Billing database tables
    access.py                  Server-side paid-access checks
    providers/                 Separate Stripe and Lemon Squeezy adapters
    templates/billing/         Pricing and membership pages
    static/js/billing.js
    README.md                  Billing setup, policies, security, debugging
.env.example                   Blank/example settings, no credentials
tests/test_billing.py          Isolated billing regression/security tests
```

Each independent app can reuse this starter with its own database and provider
products. Authentication and billing tables live in that app's same database.

## Development setup

1. Create a Python virtual environment and install dependencies:

```bash
python -m venv venv
# Git Bash on Windows:
source venv/Scripts/activate
pip install -r requirements.txt
```

2. Copy `.env.example` to `.env` and configure SMTP, DATABASE_URL, and
   JWT_SECRET_KEY. Keep `.env` private. Generate a random JWT secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

The app refuses to start with a missing, blank, or shorter-than-32-byte JWT
secret. PostgreSQL/Neon is recommended; without DATABASE_URL, auth falls back to
local SQLite for development. Auth creates missing auth tables at import time.
Use a separate database for tests and staging, never production customer data.

3. Start locally:

```bash
python app.py
```

Open http://localhost:5000/login. Billing stays disabled until configured. Debug
mode is off by default; enable it only in a private local development environment.
Do not deploy Flask's development server to production.

## Authentication

- Register and verify your email, then log in.
- Access tokens expire after one minute. Refresh tokens expire after 30 days.
- The frontend refreshes an expired access token after a protected API returns
  401. It does not run a one-minute refresh timer.
- Reloading an HTML page after access-token expiry currently redirects to login.
- Logout revokes refresh tokens. Existing access JWTs remain valid until expiry;
  logout-all/reset do not immediately invalidate already-issued access JWTs.
- `/health` is a basic application response, not an actual database connectivity
  probe. Do not use its static database label as proof of database availability.

| Method | Path | Purpose |
| --- | --- | --- |
| GET/POST | `/register` | Registration page / register |
| GET | `/verify-email` | Verify emailed token |
| GET/POST | `/login` | Login page / set auth cookies |
| POST | `/refresh` | Rotate refresh token and issue access cookie |
| GET | `/dashboard`, `/profile` | Authenticated HTML pages |
| GET | `/protected` | Example authenticated API |
| POST | `/logout`, `/logout-all` | Revoke refresh tokens and clear cookies |
| GET/POST | `/forgot-password` | Reset request page / send email |
| GET/POST | `/reset-password` | Reset page / update password |
| GET | `/health` | Basic application status |

The application has no `/change-password` endpoint. Resetting a password uses the
email flow. Browser actions use external scripts compatible with the strict CSP.

## Billing

See [billing setup and architecture](billing/README.md) for provider configuration,
webhook events, table installation, membership checks, cancellation/refund policy,
and recovery commands. No real charges or provider products are created by setup.

```bash
flask --app app billing-init-db
python -m unittest discover -s tests -v
```

Billing has verified webhook signatures, CSRF-protected actions, ownership checks,
server-owned plan IDs, transactional deduplication, PostgreSQL user-row locking,
and explicit membership expiry. Live billing enforces HTTPS, secure cookies,
PostgreSQL, and shared Redis rate-limit storage. Initial table creation is explicit;
future existing-table changes need reviewed migrations.

## Security and operational scope

Use long random secrets, production HTTPS, secure cookies, restricted database
credentials, and monitored webhooks. The default rate-limit store is memory for
local development; configure RATELIMIT_STORAGE_URI for shared limits. Authentication
still contains process-local IP/user attempt tracking and non-atomic refresh
rotation, which require a separate concurrency review before a multi-worker launch.
This project is not a claim of a completed production security audit.

Tests use a temporary in-memory database and mocked providers. Provider sandbox
flows and PostgreSQL concurrent-delivery checks are required before real payments.
See the billing documentation for the precise boundaries and remaining limitations.

## License

MIT License

Copyright (c) 2024 Carlos Gandara

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
