# Production Deployment Runbook

This repository is deployable after you provide infrastructure under your ownership. Never put production values in Git, source code, screenshots, or chat.

## 1. Create required accounts

You need: a Git provider repository, Vercel account, domain registrar/DNS account, managed PostgreSQL provider, payment provider (Stripe or a supported mobile-money provider), and code-signing certificate provider.

Enable MFA on every account before continuing.

## 2. Provision PostgreSQL

1. Create a production database in a region close to Vercel’s chosen function region.
2. Create a least-privilege database user for MyTradeBot only.
3. Require TLS and copy the provider’s PostgreSQL connection URL.
4. Configure automated backups and perform a test restore before launch.

## 3. Deploy licensing API to Vercel

1. Push this project to a private Git repository.
2. Import it into Vercel with the repository root as the project root.
3. Select Python 3.13; `.python-version` and `pyproject.toml` declare this already.
4. In Vercel Production Environment Variables, set every value in `.env.example`.
5. Generate `JWT_SECRET`, `LICENSE_SIGNING_SECRET`, and `ADMIN_BOOTSTRAP_TOKEN` separately, each at least 32 random characters.
6. Set `MYTRADEBOT_ENV=production`, a PostgreSQL `DATABASE_URL`, and `PAYMENT_PROVIDER=stripe` or `mobile_money`.
7. Deploy. Confirm `https://YOUR-VERCEL-PROJECT.vercel.app/health` reports `{"status":"ok"}`.
8. Register the configured `ADMIN_EMAIL` as the first administrator, sending `ADMIN_BOOTSTRAP_TOKEN` in the `X-Admin-Bootstrap-Token` header over HTTPS. Keep the token private.

The API should not be publicly marketed until registration, checkout, payment callback, validation, revocation, and a database backup restore have all been tested in staging.

## 4. Configure payments

### Stripe

Create monthly and annual recurring Prices in your own Stripe account. Configure `STRIPE_SECRET_KEY`, both Price IDs, callback URLs, and `STRIPE_WEBHOOK_SECRET`. Point Stripe’s webhook endpoint to:

```text
https://YOUR-API-DOMAIN/payments/webhook/stripe
```

Use Stripe test mode first. A completed Stripe Checkout callback changes a license from `pending` to `active`.

### Mobile money

Set `PAYMENT_PROVIDER=mobile_money`, `MOBILE_MONEY_CHECKOUT_URL`, and `MOBILE_MONEY_CALLBACK_SECRET`. The provider must redirect to the supplied checkout URL and send JSON `{ "reference": "LICENSE-ID", "status": "paid" }` with an `X-Payment-Signature` equal to the HMAC-SHA256 of the raw body using the configured secret. Test signatures and rejected callbacks before enabling production.

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
