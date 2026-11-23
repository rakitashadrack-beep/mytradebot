# MyTradeBot frontend

Deploy this directory as the Vercel project root. It contains only public PWA
assets and the `/config.js` runtime endpoint. Set only:

```text
MYTRADEBOT_API_URL=https://api.yourdomain.com
```

This value is public. Do not add database, payment, broker, JWT, or licensing
secrets to Vercel.
