# Separate Vercel frontend and Render backend

The public account portal and the licensing API are deployed independently:

```text
Browser / PWA  ->  Vercel static frontend  ->  Render licensing API  ->  PostgreSQL
Windows MT5 app -> Render licensing API     ->  PostgreSQL
```

The Windows MT5 controller remains local-only. Do not deploy `web_main.py`, the
`web/` trading controller, or broker credentials to either public service.

## 1. Deploy the API to Render

1. Push this repository to a private Git provider repository.
2. In Render, create a service with `apps/backend` as its root directory. It reads `render.yaml`.
3. Use the generated Render service URL, for example
   `https://mytradebot-api.onrender.com`.
4. Set every `sync: false` variable in Render. Use PostgreSQL (not SQLite) for
   `DATABASE_URL` and set three different production secrets. Render refuses to
   start with weak or incomplete production configuration.
5. Leave `PAID_PLANS_ENABLED=false` until sandbox callback and billing tests
   are complete. This does not prevent licensing/account deployment.
6. Do not set `ALLOWED_ORIGINS` yet; set it after the Vercel URL is known.
7. Deploy and verify `https://YOUR-RENDER-API/health` returns
   `{"status":"ok"}`.

## 2. Deploy the portal to Vercel

1. Import the same repository in Vercel with `apps/frontend` as the
   project root. `vercel.json` publishes only `public`; it does **not**
   deploy the Render API code.
2. In Vercel Production Environment Variables, set:

   ```text
   MYTRADEBOT_API_URL=https://YOUR-RENDER-API
   ```

   This value must be an HTTPS URL without a trailing slash. It is intentionally
   public: it only identifies the API endpoint and contains no credential.
3. Deploy and open `https://YOUR-VERCEL-SITE/config.js`. It must return a
   one-line JavaScript assignment containing the Render URL.
4. Open the Vercel site and confirm sign-in calls the Render API.

## 3. Lock the connection down

Set Render's `ALLOWED_ORIGINS` to the exact Vercel origin and the local Windows
agent origin, such as:

```text
https://mytradebot-portal.vercel.app,http://127.0.0.1:8000
```

If you attach a custom frontend domain, add it as a comma-separated additional
origin and redeploy Render. Never use `*` in production.

If M-Pesa is enabled later, `MPESA_CALLBACK_URL` must use the **Render API**
domain, not Vercel:

```text
https://YOUR-RENDER-API/payments/mpesa/YOUR_CALLBACK_TOKEN
```

## 4. Point the Windows application at Render

In the local Windows MyTradeBot app, set the License API URL to:

```text
https://YOUR-RENDER-API
```

The local trading control server is intentionally separate from the public
portal. It must continue to bind to `127.0.0.1` and only trades through the MT5
terminal running on that Windows machine.
