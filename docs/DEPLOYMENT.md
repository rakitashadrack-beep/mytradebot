# Production Deployment Runbook

This repository is deployable after you provide infrastructure under your ownership. Never put production values in Git, source code, screenshots, or chat.

## 1. Create required accounts

You need: a Git provider repository, Vercel account, domain registrar/DNS account, managed PostgreSQL provider, Africa's Talking account for SMS, and a code-signing certificate provider. For the planned Kenya launch, also provision a Safaricom Daraja merchant account.

Enable MFA on every account before continuing.

## 2. Provision PostgreSQL

1. Create a production database in a region close to Vercel’s chosen function region.
2. Create a least-privilege database user for MyTradeBot only.
3. Require TLS and copy the provider’s PostgreSQL connection URL.
4. Configure automated backups and perform a test restore before launch.

## 3. Deploy licensing API to Vercel

1. Push this project to a private Git repository.
2. Import it into Vercel with the repository root (`./`) as the project root. The root `vercel.json` builds `api/index.py` and routes requests to it. Do not select `/api` or `/backend` as a separate project root for this configuration.
3. Select Python 3.13; `.python-version` and `pyproject.toml` declare this already.
4. In Vercel Production Environment Variables, enter the required values for your selected production providers. `.env.example` contains placeholders, not usable credentials; never upload it or copy placeholder values as production settings.
5. Generate `JWT_SECRET`, `LICENSE_SIGNING_SECRET`, and `ADMIN_BOOTSTRAP_TOKEN` separately, each at least 32 random characters.
6. Set `MYTRADEBOT_ENV=production`, a PostgreSQL `DATABASE_URL`, `PAYMENT_PROVIDER=mpesa_daraja`, `MPESA_ENVIRONMENT=production`, exact allowed CORS origins, and `SMS_PROVIDER=africastalking` with its private username and API key. Enter all Daraja credentials, the HTTPS callback URL/token, and KES prices privately in the deployment settings.
7. Deploy. Confirm `https://YOUR-VERCEL-PROJECT.vercel.app/health` reports `{"status":"ok"}`.
8. Register the configured `ADMIN_EMAIL` as the first administrator, sending `ADMIN_BOOTSTRAP_TOKEN` in the `X-Admin-Bootstrap-Token` header over HTTPS. Keep the token private.

The backend serves an installable account portal at `/`. Phone OTP uses Africa's Talking; use HTTPS for deployment. Email receipts and account recovery remain unavailable until an email provider is implemented and configured. The PWA does not relay commands to the Windows trading app. Paid checkout is intentionally disabled in code (`PAID_PLANS_ENABLED=False`), and the Daraja callback flow has not been verified against Safaricom's sandbox. Do not accept payments or market paid access until the remote-PC relay, promised plan features, email, billing, callback verification, renewal/expiry, and staging tests are complete. Do not expose the unauthenticated localhost trading dashboard to the internet.

## 4. Configure payments

### Stripe

Create monthly and annual recurring Prices in your own Stripe account. Configure `STRIPE_SECRET_KEY`, both Price IDs, callback URLs, and `STRIPE_WEBHOOK_SECRET`. Point Stripe’s webhook endpoint to:

```text
https://YOUR-API-DOMAIN/payments/webhook/stripe
```

Use Stripe test mode first. A completed Stripe Checkout callback changes a license from `pending` to `active`.

### M-Pesa / Daraja (not yet enabled)

The implementation supports a customer-approved STK Push using the Daraja API. Configure `PAYMENT_PROVIDER=mpesa_daraja`, production Daraja credentials, an HTTPS callback URL ending in the exact `MPESA_CALLBACK_TOKEN`, and explicit whole-KES prices for all three plans. Callback URLs contain a secret path token; configure proxies and logs not to record that path. Never put merchant credentials or payout details in Git or chat.

This flow has not yet been verified in Daraja sandbox or production. `PAID_PLANS_ENABLED` is a code-level safety gate and is currently `False`; checkout returns HTTP 503 and successful-looking payment responses must not be expected. Do not change the gate until AI/relay features are available, callbacks are independently verified and tested, and the complete purchase/refund/expiry process has passed staging. The current STK Push is customer-initiated per renewal; it does not automatically charge monthly.

## 5. Attach your domain

1. In Vercel, add `licenses.yourdomain.com` under **Settings → Domains**.
2. Add the DNS record Vercel requests at your registrar.
3. Wait for Vercel TLS provisioning, then verify `/health` on the custom domain.
4. In the desktop app set **Strategy & Risk → Subscription → License API** to `https://licenses.yourdomain.com`.

## 6. Build and sign the Windows desktop application

On a clean Windows 11 build machine with Python 3.11:

```powershell
cd C:\path\to\mytradebot
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\build_windows.ps1
```

Compile `installer\mytradebot.iss` in Inno Setup. Sign the EXE and final installer with your Authenticode certificate. Run the signed installer in a clean Windows test VM; perform demo-account end-to-end testing before release.

## 7. Publish updates

Host signed installers and `manifest.json` only over HTTPS. The manifest must match [Phase 7](PHASE_07.md). Verify both Authenticode publisher identity and SHA-256 before publishing release links.

## 8. Launch rollback plan

- Revoke compromised licenses through the administrator endpoint.
- Disable payments in the payment-provider dashboard if fraud is suspected.
- Roll Vercel back to the previous deployment from its deployment history.
- Notify affected customers through your support channel.
- Preserve logs and database backups for incident investigation.

Assumption: a single Vercel deployment’s in-memory rate limiter is adequate only for an initial low-volume release; use a shared rate-limit service/WAF before scaling horizontally.
