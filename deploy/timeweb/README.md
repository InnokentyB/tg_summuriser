# Timeweb dogfood deployment

This is a prepared, **not yet active** deployment of the published GitHub commit
`47193aba4128a047cab5e49c927200c2956569d5` on the existing Timeweb worker
`176.57.213.115`. Its image was built and passed a network-disabled, non-root,
read-only SQLite initialization smoke on 2026-09-22. The Compose one-off smoke
also verified the persistent mount is writable by UID/GID `10001`; its test
database was then removed. GitHub Actions passed for the same commit. No
production Telegram token or database credential has been
copied to Timeweb, and the Railway bot remains the only active instance.

## Cutover gate

Do not run `docker compose up` with the production `BOT_TOKEN` while the Railway
service is online. Both instances use polling and scheduled ingestion, so a
parallel run can conflict or duplicate work.

The current Railway application `DATABASE_URL` is a private
`postgres.railway.internal` address. A Timeweb instance needs the Postgres
service's `DATABASE_PUBLIC_URL` instead; the proxy is already configured but
Railway bills network egress through it. `TGARTICLES_DATABASE_URL` already uses
a public Railway proxy. Keep all credentials in a root-owned
`/etc/tg-summuriser/runtime.env` (mode `0400`), never in Git, Docker image,
Terraform state, or command output. Require TLS for database connections and
test authenticated access before the switch.

Before production cutover:

1. Take and verify a restorable logical PostgreSQL backup outside the worker.
2. Validate the environment file, outbound Telegram/OpenAI/DB connectivity,
   and a separate test-bot run if a test bot is available.
3. Agree on a short maintenance window and explicit rollback owner. Scale the
   Railway bot service in `eu-west` to zero; do **not** scale its Postgres down.
4. Verify the old polling process stopped, start this single Timeweb container,
   and test owner commands, digest scheduling, database writes, and logs.
5. On failure, stop the Timeweb container **first**, then restore the Railway
   bot to one replica. Verify exactly one active instance and reconcile any
   work completed around the handover.

The Compose service intentionally publishes no inbound port; this bot needs
outbound connections only. The persistent `/app/data` mount must be owned by
UID/GID `10001`. Update the image tag only after building and verifying a
specific Git commit. Railway auto-deploys must be disabled or monitored during
the handover to prevent a second bot from starting unexpectedly.
