# MyTradeBot backend

Deploy this directory as the Render project root. It provides the licensing,
account, payment, and subscription API. Set all production secrets in Render,
use PostgreSQL, and configure `ALLOWED_ORIGINS` with the exact frontend domain
plus `http://127.0.0.1:8000` for the local Windows agent.

Never deploy this service with SQLite or expose its credentials to the frontend.
