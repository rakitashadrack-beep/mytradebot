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
- A Vercel static portal, separate Render API configuration, and environment-variable template are included.

## Files

```text
apps/backend/backend/      Render licensing API and storage adapter
apps/backend/render.yaml   Render API service configuration
apps/frontend/public/      Vercel public portal assets
apps/frontend/api/         Vercel runtime configuration endpoint
apps/frontend/vercel.json  Vercel static-portal routing/build configuration
apps/backend/.env.example  Required production environment variables
apps/trading-agent/core/   Desktop paid-subscription validation and activation
tests_backend/             Backend flow tests
tests/test_licensing.py    Desktop licensing tests
```

## Deploy frontend and API independently

Deploy `apps/backend` to Render for the API and PostgreSQL connection. Deploy
`apps/frontend` to Vercel for the static portal, configuring only
`MYTRADEBOT_API_URL` there. Configure the Render API's `ALLOWED_ORIGINS` with
the exact Vercel portal URL and `http://127.0.0.1:8000` for the local Windows
agent. The complete sequence is in
[the separate deployment guide](SEPARATE_FRONTEND_BACKEND_DEPLOYMENT.md).

## Local backend run and test

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r apps/backend/backend/requirements.txt
export PYTHONPATH=apps/backend
uvicorn backend.app:app --reload
pytest tests_backend tests/test_licensing.py -q
```

Assumption: real Stripe/mobile-money webhooks require your merchant accounts and are deliberately not enabled without them. Keep the administrator bootstrap token private; the existing administrator account cannot be recreated through public registration.
