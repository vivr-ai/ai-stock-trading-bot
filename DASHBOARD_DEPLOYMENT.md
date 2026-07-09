# Deploying the dashboard (Phase 1: database + Home page)

I don't have a Railway connector available in this session, so I couldn't
provision anything on Railway directly. Everything below is code you already
have - these are the manual steps to wire it up in Railway's dashboard
(about 10 minutes, all clicking, no typing code).

## 1. Add a Postgres database to the project

In your Railway project (the one that already has the bot service):

1. Click **New** -> **Database** -> **Add PostgreSQL**.
2. That's it - Railway provisions it and exposes a `DATABASE_URL` variable
   on the Postgres service itself.

## 2. Give the bot service access to the database

1. Open the **bot service** -> **Variables** tab.
2. Add a new variable: `DATABASE_URL` -> click the reference icon and select
   `Postgres.DATABASE_URL` (or paste `${{Postgres.DATABASE_URL}}`).
3. Redeploy the bot service. From this point on, every cycle writes to the
   database in parallel with its existing CSV/JSON logs - nothing about the
   trading logic changes.

## 3. Apply the database schema (one-time)

Easiest path - Railway's web shell:

1. Open the **bot service** -> click the three-dot menu -> **Shell** (or use
   `railway run` locally if you have the CLI installed and linked).
2. Run:
   ```
   python scripts/init_db.py
   ```
   This applies `db/schema.sql`. Safe to re-run any time.

## 4. Create the dashboard as a second service

1. In the same Railway project: **New** -> **GitHub Repo** -> pick this same
   repo again.
2. Once created, open its **Settings** tab -> **Root Directory** -> set to
   `dashboard`.
3. Go to **Variables** and add:
   - `DATABASE_URL` -> reference `Postgres.DATABASE_URL` (same as step 2)
   - `DASHBOARD_PASSWORD` -> pick a password for now (Phase 1 - see below)
   - `NEXTAUTH_SECRET` -> any long random string (e.g. run
     `openssl rand -base64 32` locally and paste the result)
   - `NEXTAUTH_URL` -> leave blank until step 5, then come back and set it
4. Deploy. Railway will detect it's a Next.js app automatically (Nixpacks).

## 5. Set the public URL

1. Once deployed, open the dashboard service -> **Settings** -> **Networking**
   -> **Generate Domain** (or attach a custom domain).
2. Copy that URL, go back to **Variables**, and set `NEXTAUTH_URL` to it
   (e.g. `https://your-dashboard.up.railway.app`). Redeploy once more.

## 6. Log in

Visit the dashboard URL and enter the `DASHBOARD_PASSWORD` you set in step 4.

Note on auth: this is intentionally a single shared password for now (as
agreed) but built on NextAuth, so swapping in real per-user accounts later
(email/password with a `users` table, or Google/GitHub sign-in) is a small
change to `dashboard/lib/auth.ts` - the rest of the app (middleware, pages)
already just checks "is there a valid session," not how it was obtained.

## What you should see

The Home page will show "No data yet" until the bot's scheduler runs its
next cycle (every 30 min during market hours) with `DATABASE_URL` configured.
After that, it fills in automatically - no redeploy needed, the page polls
every 60 seconds.

## Troubleshooting

- **Home page shows an error, not "No data yet"**: almost always
  `DATABASE_URL` is missing or wrong on the dashboard service - check
  Variables.
- **Bot logs mention "psycopg2-binary isn't installed"**: the bot's most
  recent deploy predates this change - redeploy so it picks up the new
  `requirements.txt`.
- **Login redirects back to itself**: `NEXTAUTH_URL` doesn't match the
  actual public URL, or `NEXTAUTH_SECRET` isn't set.
