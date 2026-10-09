# Discito — Verse Memorizer

Self-hosted app that takes a child from "never seen this poem" to "recited it
from memory three times clean": TTS reads each line, the child recites the chain
1..n into the mic, a faster-whisper sidecar transcribes, and the app grades by
aligning the transcript against the known text. A second tab does spelling
lists with spoken prompts and typed answers. One household, per-child progress,
a parent area for assignments, attempt review, overrides, and trouble spots.

FastAPI + Jinja2 + Tailwind CSS v4, SQLAlchemy/Alembic on SQLite, uv. Two
containers (app + whisper) via `docker-compose.yml` on Coolify. The PRD lives
in `initial_prd.md`.

## Layout

```
app/core/        pure logic, unit-tested: text normalization, sectioning,
                 alignment/grading, struggle score, chain + spelling schedulers
app/services/    DB-aware flows: recite attempts/overrides/re-sectioning,
                 spelling sessions/carry-forward, dashboard stats, whisper client
app/routes/      kids (picker, home, practice pages), api (JSON for practice JS),
                 admin (/parent/*), health
app/templates/   Jinja2; app/static/js/{recite,spell,common}.js drive practice
whisper/         faster-whisper sidecar (own pyproject, Dockerfile)
alembic/         migrations (run on container start)
```

## Local development

```bash
uv sync
npm install && npm run css            # or css:watch; styles.css is a build artifact
cp .env.example .env                  # STT_TYPED_FALLBACK=1 adds a "type what you said" box
uv run alembic upgrade head
uv run python -m app.cli seed         # optional: 2 kids, Psalm 23, Frost, a spelling list
uv run uvicorn app.main:app --reload --port 8013
```

Real speech locally: run the sidecar and point `WHISPER_URL` at it.
`tiny.en` downloads fast and is fine for checking the plumbing:

```bash
cd whisper && uv sync && WHISPER_MODEL=tiny.en uv run uvicorn app:app --port 9000
```

The mic needs a secure context: `localhost` works; a LAN IP over plain http does not.

Checks: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy app`.
App tests run through the real Alembic migrations on a fresh SQLite file.

## Deploy (Coolify)

1. New resource → this repo → build pack **Docker Compose** (`docker-compose.yml`).
2. Domain on the **app** service only, port **8000**; Traefik provisions Let's
   Encrypt (HTTPS is required for the mic prompt). The whisper service has no
   ports and sits on an internal-only network.
3. Environment: `SECRET_KEY`, `PARENT_PASSWORD` (required), optionally
   `HOUSEHOLD_PASSWORD`, `TIMEZONE`, `WHISPER_MODEL`.
4. The named volume `discito-data` is mounted at `/data`; `/data/discito.db` is
   the only state. Add it to the homelab B2 backup job (use `sqlite3 .backup`
   or snapshot while idle; WAL mode is on).

The whisper image bakes the model in at build time (`WHISPER_MODEL` build arg,
default `small.en`), so first start doesn't download from Hugging Face.

| Env var | Default | Notes |
|---|---|---|
| `SECRET_KEY` | — | signs the session cookie; 32+ random bytes |
| `PARENT_PASSWORD` | — | parent area login (12-hour session) |
| `HOUSEHOLD_PASSWORD` | *(empty)* | if set, each device enters it once before the child picker shows |
| `TIMEZONE` | `America/Los_Angeles` | streak day boundaries, attempt times |
| `WHISPER_MODEL` | `small.en` | switch to `base.en` if grading is too slow on the VPS |
| `WHISPER_CPU_THREADS` | `0` (all) | |
| `STT_TYPED_FALLBACK` | off | dev only |
| `PEEK_INVALIDATES` | off | a peek fails the step's clean run |

## Behavior decisions (where the PRD left room)

- **Drills in partial practice** fire when a line is missed *again* while still
  flagged. A first miss replays the line for a retry. (With α = 0.5, one miss
  already crosses the 0.34 flag, so "flagged → drill" would otherwise drill
  every mistake.) In Full mode every failed run drills its missed lines, per the PRD.
- **Parent overrides** are always recorded. Overriding the child's *latest*
  attempt also replays it from a snapshot, so marking a mis-heard line
  "correct" unlocks the next line. Overrides of older attempts are audit-only.
- **Clean attempts below 100%** (threshold < 100%) don't count misses against
  lines; only failed attempts move struggle scores.
- **Un-assigning** a child from a passage or list only removes assignments that
  haven't been started, so a stray checkbox can't wipe progress.
- **Editing passage text** keeps unchanged sections (and their stats) and
  rolls each child back to the first changed section if they were past it.
- **Practice / test resume**: leaving a practice round for Learn, Test, or the
  menu keeps its place; a paused test resumes where it stopped.
- **Password forms lock out an address** after 5 wrong tries in 15 minutes
  (parent login and household unlock share the counter; it lives in memory).
- **No audio is persisted.** It is held in memory for the sidecar call only.
  The PRD's "attempt review with audio" conflicts with this; review shows the
  transcript with misses highlighted.

Open questions from the PRD, as defaulted: carry-forward **on**; Whisper
**`small.en`** (env switch to `base.en`); a peek **does not** invalidate a
clean run (it's logged and shown to the parent; `PEEK_INVALIDATES=1` flips it);
homophones come from a **hand-maintained seed file**, `app/core/homophones.txt`.

## Stack notes

Matches the other Coolify apps: uv/pyproject, pydantic-settings, SQLAlchemy 2 +
Alembic, structlog JSON logs, ruff + mypy strict, pytest (agile_exec API), and
Jinja2 + Tailwind v4 built in a node stage, self-hosted assets, strict
`'self'` CSP, non-root user, `/healthz` (wagconsult). Practice pages use small
vanilla JS controllers instead of HTMX/Alpine, so the CSP stays strict
(Alpine needs `unsafe-eval`).
