# Billing

Optional subscription billing for one application and its own users/database.
Stripe and Lemon Squeezy are separate adapters. No shared account system between
independent apps is introduced. Billing is disabled by default.

## Boundaries

| File | Responsibility |
| --- | --- |
| `__init__.py` | Register billing and maintenance commands |
| `config.py` | Validate environment, canonical origin, provider credentials, plan catalog |
| `routes.py` | HTTP input, authentication, CSRF, request limits, safe errors |
| `service.py` | Checkout coordination, transactional webhook processing, membership rules |
| `models.py` | Checkout intents, subscription projections, processed-event receipts |
| `access.py` | Verified-user and paid-access decorators |
| `providers/` | Provider API calls, signatures, normalized events/subscriptions |
| `templates/billing/` | Pricing and membership pages |
| `static/js/billing.js` | Checkout and portal actions; no secrets or access decisions |
| `static/js/checkout-overlay.js` | Lemon.js overlay lifecycle and bounded confirmation polling |
| `security.py` | Payment script/frame permissions scoped to the pricing page |

The tables use the existing SQLAlchemy engine and `users.id`. No card numbers,
raw payment tokens, raw webhook bodies, or API credentials are stored in billing
tables. Checkout URLs are temporary credentials: restrict database access and
never log them. The module stores only the data needed to link and authorize
subscriptions. There is no public endpoint for modifying membership status.

## Local setup

1. Install `requirements.txt` and configure authentication as described in the
   root README. Use test credentials first.
2. Create a recurring, fixed-price membership in your provider dashboard. For
   Stripe, enable/configure the hosted customer portal. For Lemon Squeezy, use
   standard subscription pricing, not metered or pay-what-you-want pricing.
3. Add ONE of the following examples to your private `.env`, replacing example
   IDs and secrets with your own. Both adapters may be enabled, but a user can
   have only one current subscription in this app. A plan maps to one provider;
   switching providers does not migrate existing subscriptions automatically.

Stripe example:

```dotenv
BILLING_ENABLED=true
BILLING_MODE=test
BASE_URL=http://localhost:5000
STRIPE_SECRET_KEY=sk_test_REPLACE
STRIPE_WEBHOOK_SECRET=whsec_REPLACE_WITH_ENDPOINT_SECRET
BILLING_PLANS='{"monthly":{"name":"Monthly membership","description":"Access to paid features","price_label":"$10/month","provider":"stripe","product_id":"price_REPLACE"}}'
```

Lemon Squeezy example:

```dotenv
BILLING_ENABLED=true
BILLING_MODE=test
BASE_URL=http://localhost:5000
LEMONSQUEEZY_API_KEY=REPLACE
LEMONSQUEEZY_STORE_ID=123
LEMONSQUEEZY_WEBHOOK_SECRET=REPLACE_WITH_RANDOM_32_TO_40_CHARACTERS
BILLING_PLANS='{"monthly":{"name":"Monthly membership","description":"Access to paid features","price_label":"$10/month","provider":"lemonsqueezy","product_id":"456"}}'
```

For Lemon Squeezy, `product_id` means the variant ID; for Stripe, it means the
recurring price ID. IDs are strings. `price_label` is presentation only: the
provider determines the charge, so keep this text consistent with provider
pricing. A one-time payment product is rejected before creating checkout.
Trials follow the provider's product configuration. There is no local free-trial
counter or free access based on a browser flag.

4. Before accepting checkout, create the initial billing tables explicitly:

```bash
flask --app app billing-init-db
```

This creates missing billing tables without changing existing auth data. It is
an initial schema installer, not a migration engine. Future changes to existing
columns require a reviewed schema migration; `create_all` will not alter them.

5. Configure webhooks at the provider. For local testing, use Stripe CLI or a
   trusted HTTPS tunnel. Keep `BASE_URL` equal to the origin actually used by the
   browser: mixing localhost and 127.0.0.1 will fail the Origin check.
6. Start the app, register/verify/log in, and visit `/billing/pricing`.

Billing does not change `.env`, create products, charge cards, or change your Neon
schema automatically. Real account configuration and a provider sandbox checkout
are still required before enabling live billing.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/billing/pricing` | Public pricing page and browser CSRF session |
| GET | `/billing/membership` | Verified user's own subscription status |
| GET | `/billing/api/membership` | Same status as JSON |
| POST | `/billing/checkout` | Create/reuse checkout for a server-defined plan |
| POST | `/billing/portal` | Manage an owned subscription at the provider |
| POST | `/billing/webhooks/stripe` | Stripe-signed events |
| POST | `/billing/webhooks/lemonsqueezy` | Lemon Squeezy-signed events |

Browser POSTs require a signed Flask session CSRF token in `X-CSRF-Token`, an
exact canonical `Origin`, verified login, and JSON input. User/customer IDs,
prices, return URLs, and provider selections cannot be supplied by the browser.
Only signed webhook endpoints bypass browser CSRF and IP-based application rate
limits; constrain webhook traffic at the edge as well. Bodies are limited to
256 KiB for webhooks and 4 KiB for billing actions. Responses are not cached.

## Provider events

### Checkout presentation

Lemon Squeezy Subscribe opens the official Lemon.js checkout overlay, using the
same server-created, account-linked checkout URL. Customers can close it and
reopen the pending checkout. A full-page link remains available, and missing or
failing overlay scripts fall back to hosted checkout without creating a second
checkout. Stripe checkout and billing portals continue to use full-page redirects.

Only the pricing page loads Lemon.js. Its content security policy permits the
documented script URL and its exact CDN redirect (`assets.lemonsqueezy.com/lemon.js`),
plus HTTPS Lemon Squeezy frames. Login, membership, and API policies stay unchanged.
Inline scripts and arbitrary inline styles remain blocked. External presentation
styles cover the vendor's generated loader and frame; provider-generated inline
style attempts may still appear as blocked CSP messages in browser diagnostics.

The vendor's Checkout.Success callback is presentation only. The page checks the
authenticated membership API up to eight times over approximately 28 seconds,
waiting for a matching plan with server-confirmed access. It then opens Membership.
If confirmation is delayed or the network fails, the page points to Membership
and explains that another payment is unnecessary. No order data from the callback
is used to grant access. Test checkout, close/reopen, and payment-method behavior
in your supported browsers before enabling live payments.

Run browser-script checks without a network or actual payments:

```bash
node tests/test_billing_browser.cjs
node tests/test_checkout_overlay.cjs
```

### Webhook destinations

Stripe: configure a **snapshot** webhook destination using API version
`2025-06-30.basil`, matching the adapter's pinned REST API version, for:

```text
customer.subscription.created
customer.subscription.updated
customer.subscription.deleted
invoice.paid
invoice.payment_failed
invoice.payment_action_required
charge.refunded
charge.dispute.created
```

Lemon Squeezy: subscribe to:

```text
subscription_created
subscription_updated
subscription_cancelled
subscription_resumed
subscription_expired
subscription_paused
subscription_unpaused
subscription_payment_success
subscription_payment_failed
subscription_payment_recovered
subscription_payment_refunded
```

The adapters validate signatures against the exact raw bytes. Stripe additionally
requires a signed timestamp within five minutes. Lemon Squeezy does not provide
an equivalent signed timestamp; its raw-payload hash is the delivery identity,
and current subscription state is fetched from the authenticated API rather than
trusting an old signed payload. Lemon Squeezy store and test/live mode are checked.

Events with an unknown checkout reference are rejected, not linked by email.
Subscriptions created manually outside this app need an explicit operator-led
import; they cannot grant access just by matching a user's address.

## Access and cancellation policy

Use the public decorator on every paid page AND paid API route:

```python
from billing.access import membership_required

@app.get('/paid-feature')
@membership_required()  # Or membership_required('monthly') for a specific plan.
def paid_feature():
    return {'message': 'Paid feature'}
```

The existing dashboard/profile remain free authenticated pages. Membership lives
in the database, never inside a long-lived JWT claim or a browser variable.

- Access requires a recognized configured plan, active/trial status, and a
  future provider-supplied access deadline. Removing a plan denies its access;
  retain old plan entries while grandfathered subscribers should keep access.
- Stripe scheduled cancellation retains active access until its deadline.
  Lemon Squeezy `cancelled` retains access until `ends_at`.
- `past_due`, unpaid, paused, expired, unknown, or missing-deadline states deny
  access. This intentionally has no payment-failure grace period.
- Subscription refunds (including partial refunds) and Stripe disputes block
  access pending support review. This block survives later subscription updates.
  Refunds do not automatically cancel future billing: the operator must review
  and cancel or restore service using the provider dashboard and an authorized
  database change. There is no browser/admin bypass endpoint.
- Lemon Squeezy chargeback handling is limited to the events it delivers. Monitor
  provider notices and review accounts when the provider cannot send a matching
  subscription event. Do not assume all financial disputes are covered by a
  subscription lifecycle event.

Checkout return URLs never activate access. A pending checkout is saved before
calling the provider so fast webhooks can resolve it. Repeated clicks reuse the
same unexpired checkout. An ambiguous network failure stays pending for up to
one hour instead of retrying a potentially successful payment POST. Do not
delete pending records to force retries without checking provider state first.

If the short-lived access cookie expires during hosted checkout, the membership
HTML route serves a page with no private data. Its external script calls the
protected membership API; the existing authenticated-fetch helper refreshes the
session using POST /refresh and retries. The page reloads only after authentication
succeeds. Missing/invalid refresh credentials send the user to login; a network
failure shows a retry action. No payment is repeated and GET requests do not
rotate tokens. The API and billing actions remain authenticated.

## Concurrency, recovery, and deployment

On PostgreSQL, checkout and webhook transactions lock the owning user row. This
serializes updates across workers for that account while allowing unrelated
users to proceed independently. Unique constraints deduplicate deliveries and
subscription IDs. The receipt and subscription update commit in one transaction;
a database or provider failure rolls back both so provider retries are safe.
Current provider state is read under the lock, avoiding resurrection from delayed
subscription events. No process-local billing membership cache is used.

Processing is synchronous with bounded network timeouts and no automatic POST
retries. This is a modest deployment starting point, not an unlimited-throughput
queue. At higher webhook volume, place a durable verified-event inbox/worker
before the same service boundary. Do not acknowledge a webhook before either
the transaction or durable enqueue succeeds.

Run reconciliation regularly through your deployment scheduler and after a
webhook outage:

```bash
flask --app app billing-reconcile
```

It reads subscriptions in batches and refreshes their provider state. It exits
nonzero on failures. It does not discover purchases with no local subscription,
or replay refund/dispute actions: monitor and redeliver failed provider events.
Access deadlines still expire without a background job, but immediate changes
depend on webhook delivery or reconciliation. No scheduler is installed here.

Live mode refuses startup without HTTPS `BASE_URL`, `COOKIE_SECURE=True`, secure
session cookies, PostgreSQL, Redis rate-limit storage, matching provider mode,
and configured secrets. Install the Redis dependency and provision Redis first.
Use a production WSGI server behind HTTPS; `python app.py` is for development.
The canonical Origin check uses configuration rather than untrusted Host headers.
If reverse proxies must supply client IPs, configure trusted proxy counts for
your actual deployment; do not blindly trust forwarded headers from the internet.

For test and live, use separate deployments/databases and credentials. Database
queries also scope mode explicitly. Plan keys/IDs and historical webhook receipts
must survive deployments. Keep receipts to preserve replay protection; establish
an operational retention policy before pruning. Never expose the database,
provider secrets, checkout references/URLs, or signed portal links publicly.

## Debugging and verification

Billing failures include `X-Request-ID`; server failure logs include that ID.
Successful events log provider, event ID, and subscription ID. HTTP adapter logs
include status/error type without response bodies or credentials. Do not enable
verbose HTTP-wire logging in production. Inspect provider delivery logs and
`billing_webhook_receipts` when debugging retries; the receipt exists only after
the corresponding state transaction succeeds.

Run the isolated security/regression suite:

```bash
python -m unittest discover -s tests -v
```

Tests use in-memory SQLite, mocked provider APIs, synthetic signing secrets, and
no real customer data. They verify authentication, ownership, CSRF, signatures,
mode isolation, duplicates, stale events, deadlines, refunds, rollback/retry,
checkout reuse, provider request contracts, and recovery. SQLite does not test
PostgreSQL locking. Before live use, exercise concurrent deliveries on a staging
PostgreSQL database plus real provider sandbox checkout/portal/webhook flows.

This is not a full audit of the pre-existing authentication system. Existing
access JWTs can remain valid for their one-minute lifetime after logout/reset,
and the original refresh rotation/account throttling code needs its own review
for multi-worker concurrency. Billing adds database access checks; it does not
claim to repair every pre-existing authentication limitation.

## Provider references

- [Stripe Checkout](https://docs.stripe.com/api/checkout/sessions/create)
- [Stripe webhooks](https://docs.stripe.com/webhooks)
- [Stripe customer portal](https://docs.stripe.com/api/customer_portal/sessions/create)
- [Stripe invoice payments](https://docs.stripe.com/api/invoice-payment/list)
- [Lemon Squeezy checkout](https://docs.lemonsqueezy.com/api/checkouts/create-checkout)
- [Lemon Squeezy signatures](https://docs.lemonsqueezy.com/help/webhooks/signing-requests)
- [Lemon Squeezy subscription lifecycle](https://docs.lemonsqueezy.com/api/subscriptions/the-subscription-object)
