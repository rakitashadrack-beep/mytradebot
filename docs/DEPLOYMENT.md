# Production Deployment Runbook

This repository is deployable after you provide infrastructure under your ownership. Never put production values in Git, source code, screenshots, or chat.

## 1. Create required accounts

You need: a Git provider repository, Vercel account, domain registrar/DNS account, managed PostgreSQL provider, Africa's Talking account for SMS, and a code-signing certificate provider. For the planned Kenya launch, also provision a Safaricom Daraja merchant account.

Enable MFA on every account before continuing.

## 2. Provision PostgreSQL

1. Create a production database in a region close to the Render API region.
2. Create a least-privilege database user for MyTradeBot only.
3. Require TLS and copy the provider’s PostgreSQL connection URL.
4. Configure automated backups and perform a test restore before launch.

## 3. Deploy the separate services

Use one private Git repository with three independently deployed components. This keeps shared contracts and releases together while allowing each component to deploy independently:

```text
Vercel frontend (public PWA)  ->  Render API  ->  PostgreSQL
Windows MT5 trading agent     ->  Render API  ->  PostgreSQL
```

1. Push this project to a private Git repository.
2. Create the **Render API** with `apps/backend` as its project root. Set its `sync: false` values privately: PostgreSQL `DATABASE_URL`, `JWT_SECRET`, `LICENSE_SIGNING_SECRET`, `ADMIN_BOOTSTRAP_TOKEN`, Africa's Talking credentials, and any future payment credentials. Confirm `https://YOUR-RENDER-API/health` returns `{"status":"ok"}`.
3. Create the **Vercel frontend** with `apps/frontend` as its project root. It publishes only `public`; it does not deploy the API. Set only `MYTRADEBOT_API_URL=https://YOUR-RENDER-API` in Vercel. This is a public endpoint URL, not a secret.
4. Open `https://YOUR-VERCEL-SITE/config.js` and confirm it returns the configured Render API URL. Set Render's `ALLOWED_ORIGINS` to the exact Vercel URL, then redeploy Render.
5. Register the configured `ADMIN_EMAIL` through the Render API, sending `ADMIN_BOOTSTRAP_TOKEN` in the `X-Admin-Bootstrap-Token` header over HTTPS. Keep the token private.

The public PWA is an account and subscription portal only. It never has access to MT5 or broker credentials, and it does not relay bot commands. The Windows agent remains local-only. Do not expose the local trading controller to the internet.

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

1. In Render, add `api.yourdomain.com` under the service's custom-domain settings.
2. In Vercel, add `app.yourdomain.com` under **Settings → Domains**.
3. Add the DNS records each provider requests and wait for TLS provisioning.
4. Verify `https://api.yourdomain.com/health`, set Vercel `MYTRADEBOT_API_URL=https://api.yourdomain.com`, then set Render `ALLOWED_ORIGINS=https://app.yourdomain.com`.
5. In the desktop app set **Strategy & Risk → Subscription → License API** to `https://api.yourdomain.com`.

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
