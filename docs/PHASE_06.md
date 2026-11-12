# Phase 6 — Licensing and subscriptions backend

Status: complete and unit-tested locally.

## Delivered

- FastAPI licensing service: registration, authenticated login, subscription provisioning, account-and-device-bound license validation, revocation, and administrator dashboard data.
- Passwords are hashed with bcrypt; API sessions and six-hour license assertions are signed JWTs.
- License checks bind only an MT5 account number and a one-way device hash; broker passwords are neither accepted nor stored.
- Desktop application requires a configured license API and an active paid subscription before automated trading begins, except for the explicitly configured developer test account. First activation signs in to the customer account and binds its entitlement to the MT5 account and device. The entitlement is revalidated every five minutes and fails closed on service errors.
- Free trials do not authorize automated trading. Manual checkout requests remain pending; only verified provider callbacks activate a paid entitlement.
- Paid M-Pesa checkout remains disabled until its sandbox flow, renewal, and production configuration are verified.
- PostgreSQL is required in production; SQLite is exclusively supported for local development/tests.
- Process-local rate limiting is active. Deploy one instance or put a shared rate limiter/reverse proxy in front of multi-instance production deployments.
- Vercel entrypoint, deployment configuration, and environment-variable template are included.

## Files

```text
backend/app.py             Licensing API and storage adapter
backend/requirements.txt   Backend dependencies
api/index.py               Vercel ASGI entrypoint
vercel.json                Vercel routing/build configuration
.env.example               Required production environment variables
core/licensing.py          Desktop paid-subscription validation and activation
tests_backend/             Backend flow tests
tests/test_licensing.py    Desktop licensing tests
```

## Deploy to Vercel after you own a domain

1. Create a managed PostgreSQL database from a provider you control; copy its TLS connection URL.
2. Import this repository into Vercel and select the project root as the deploy directory.
3. Add every value from `.env.example` as a Vercel Production Environment Variable, including a unique 32+ character `ADMIN_BOOTSTRAP_TOKEN`.
4. Deploy, then verify `https://your-vercel-project.vercel.app/health` returns `{"status":"ok"}`.
5. Add your domain in **Vercel → Project → Settings → Domains**, then place its final `https://licenses.yourdomain.com` URL in MyTradeBot’s **Strategy & Risk → Subscription → License API** field.
6. Register the owner account using `ADMIN_EMAIL` and send `ADMIN_BOOTSTRAP_TOKEN` in the `X-Admin-Bootstrap-Token` request header. This token is required only for initial administrator registration; keep it private and do not send it over plain HTTP. Do not use the development defaults in production.

Vercel itself does not include a persistent PostgreSQL database, so `DATABASE_URL` must point to your external managed PostgreSQL service.

## Local backend run and test

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.app:app --reload
pytest tests_backend tests/test_licensing.py -q
```

Assumption: real Stripe/mobile-money webhooks require your merchant accounts and are deliberately not enabled without them. Keep the administrator bootstrap token private; the existing administrator account cannot be recreated through public registration.
