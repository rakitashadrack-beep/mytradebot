# MyTradeBot Pre-launch Checklist

Do not launch or sell until every applicable item is complete and independently checked.

## Product safety

- [ ] Run `pytest -q tests tests_backend` on the release commit.
- [ ] Complete a Windows demo-account integration run: connect, manual trade, automated trade, stop loss, take profit, close position, disconnect, reconnect, and limit-hit behavior.
- [ ] Verify no order opens without a stop loss or after daily/drawdown/weekly limits.
- [ ] Verify symbol suffix, trade-mode, stop-level, margin, and spread behavior with the target broker.
- [ ] Conduct at least several weeks of demo forward testing under normal and volatile conditions.
- [ ] Record known limitations, including lack of profit guarantees and dependence on terminal/network availability.

## Security and licensing

- [ ] Deploy PostgreSQL with backups, TLS, least-privilege database access, and a tested restore.
- [ ] Set unique production secrets in Vercel; never commit `.env`.
- [ ] Test registration, login, subscription checkout, payment webhook, validation, offline grace, and revocation on a staging service.
- [ ] Verify first-admin registration fails without the bootstrap token and succeeds only for the configured owner email.
- [ ] Configure a shared rate limit/WAF for production if running multiple API instances.
- [ ] Enable account MFA at Vercel, database, domain registrar, source control, and payment provider.

## Legal and commercial

- [ ] Obtain legal review of the terms, risk disclosure, privacy notice, refunds, tax, and applicable marketing rules.
- [ ] Configure approved Stripe/mobile-money accounts, webhooks, receipts, refunds, and support process.
- [ ] Confirm broker terms permit the intended automation and marketing use.
- [ ] Remove every claim that implies guaranteed profits or “winning” trades.

## Windows release

- [ ] Build on clean Windows 11 with Python 3.11 and run the installer on a separate clean Windows machine.
- [ ] Obtain an Authenticode code-signing certificate and sign the EXE and installer.
- [ ] Host installer and signed update manifest over HTTPS; verify SHA-256 and signature before publishing.
- [ ] Document rollback and emergency-disable procedures.

## Operational launch gate

- [ ] Set up monitoring, error alerts, incident owner, and customer-support channel.
- [ ] Run a final vulnerability/dependency review.
- [ ] Confirm the production `/health` endpoint, custom domain TLS certificate, and payment webhook signatures from outside the development network.
- [ ] Approve a versioned release note and backup the release artifacts.
- [ ] Launch to a small beta group before public release.
