# Comment Agent

An in-app AI **comment agent** for content creators. It reads the comments on a creator's posts, classifies each into a category, and — based on a per-category policy the creator controls — drafts a reply, auto-sends a low-risk reply, hides spam, or flags the comment for human review. Replies are written in the creator's own voice, learned from their past replies via semantic retrieval (RAG).

**v1 scope:** YouTube only · comment agent only · classify + draft-and-approve first (auto-send for low-risk categories comes after the draft path is proven). The architecture is platform-neutral above the integration layer, so other platforms slot in later without touching the agent.

> See `CLAUDE.md` for the full design, architecture, security requirements, error-handling policy, and the phased build plan. This README covers running the project locally and the deployment seams.

---

## Tech stack

- **Backend:** Python · FastAPI
- **Frontend:** React · Vite · Tailwind CSS
- **Database:** PostgreSQL + `pgvector` (semantic voice retrieval)
- **Migrations:** Alembic
- **Local DB:** Postgres in Docker (docker-compose), pgvector-enabled image
- **Encryption:** `cryptography` (Fernet) for user secrets at rest
- **LLM:** bring-your-own-key — each creator supplies their own provider/model and API key. There is no app-owned LLM subscription; every agent LLM call uses the requesting creator's stored, encrypted key.

---

## Repository layout

```
/
  backend/            FastAPI app, Alembic migrations, all server code
  frontend/           React + Vite + Tailwind app
  docker-compose.yml  local Postgres + pgvector
  CLAUDE.md           full design + phased build plan
  README.md           this file
```

The monorepo deploys to two targets: **Vercel root = `frontend/`**, **Render root = `backend/`**.

---

## Prerequisites

- Docker + Docker Compose
- Python 3.11+ (for the backend)
- Node 18+ (for the frontend)
- A Google Cloud project with the **YouTube Data API v3** enabled, and OAuth 2.0 credentials (needed to read comments and post replies)
- An API key for at least one LLM provider, to test the bring-your-own-key flow

---

## Environment variables

Never commit real values. Each side has a `.env.example` listing variable names with empty placeholders; copy it to `.env` and fill in locally.

### Backend (`backend/.env`) — server-side only, never shipped to the browser

| Variable                | Purpose                                                                                      |
| ----------------------- | -------------------------------------------------------------------------------------------- |
| `DATABASE_URL`          | Postgres connection. Local Docker URL in dev, Neon URL in prod.                              |
| `ENCRYPTION_KEY`        | Fernet key. **The crown jewel** — decrypts all user secrets. Never in the repo, DB, or logs. |
| `JWT_SECRET`            | Signs/verifies auth JWTs. Second crown-jewel secret; env-only, never in repo/DB/logs.        |
| `FRONTEND_ORIGIN`       | Allowed CORS origin. `http://localhost:5173` in dev, the Vercel domain in prod.              |
| `YOUTUBE_CLIENT_ID`     | YouTube OAuth client id.                                                                     |
| `YOUTUBE_CLIENT_SECRET` | YouTube OAuth client secret (server-side only).                                              |

### Frontend (`frontend/.env`) — `VITE_`-prefixed, **shipped to the browser, never put a secret here**

| Variable            | Purpose                                                              |
| ------------------- | -------------------------------------------------------------------- |
| `VITE_API_BASE_URL` | Backend URL. `http://localhost:8000` in dev, the Render URL in prod. |

> Generate a Fernet key for `ENCRYPTION_KEY` with:
> `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`

---

## Running locally

### 1. Start the database

```bash
docker compose up -d
```

This brings up Postgres with `pgvector`. Confirm it is healthy before continuing.

### 2. Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                 # then fill in values
alembic upgrade head                                 # applies schema + enables pgvector
uvicorn app.main:app --reload --port 8000
```

Backend is up when `GET http://localhost:8000/health` returns OK.

### 3. Frontend

```bash
cd frontend
npm install
cp .env.example .env                                 # then fill in VITE_API_BASE_URL
npm run dev
```

Frontend dev server runs on `http://localhost:5173` by default.

### 4. Connect accounts

In the running app: connect a YouTube account (OAuth), then add an LLM provider + API key under settings. Both are stored encrypted at rest.

---

## Database & migrations

- Local dev and production use the **same** Postgres + pgvector — no code changes between them, only the `DATABASE_URL`.
- Primary keys are **UUIDs**; platform-native IDs (YouTube comment/post/account ids) are stored as their own columns, never as primary keys.
- The `pgvector` extension is enabled by an early migration so local and prod stay consistent.
- Apply migrations with `alembic upgrade head`. Create new ones with `alembic revision --autogenerate -m "message"` (review autogenerated migrations before committing — autogenerate does not always capture pgvector/index details correctly).
- **In production, run migrations as a deliberate one-off** against the Neon URL, not automatically on every Render boot.

---

## Security notes (summary — full policy in `CLAUDE.md`)

- User LLM API keys and YouTube OAuth tokens are **encrypted at rest** (Fernet). Encrypt before write, decrypt only in memory at point of use.
- `ENCRYPTION_KEY` lives only in the deployment env / local `.env`. Its leak equals every user key leaking.
- The UI only ever shows a **masked** key (e.g. `sk-...4f2a`); the full plaintext key is never returned to the frontend after it is saved.
- Keys are **scrubbed from all logs**, error messages, and tracebacks — especially around LLM calls.
- Every decrypt path enforces **per-user ownership**: one creator's run can never use another's key.
- Keys can be **validated on entry, rotated, and hard-deleted**.
- Users are advised to set a **spending cap** on their provider account so a worst-case leak is bounded.
- **Auth:** creators sign up / log in with a password hashed via bcrypt/argon2 (plaintext never stored or logged). Auth is **stateless JWT**: a short-lived signed token (`JWT_SECRET`) in an `HttpOnly`, `Secure` cookie, plus a **double-submit CSRF token** (a separate non-HttpOnly cookie echoed back in an `X-CSRF-Token` header on state-changing requests). An auth middleware verifies the JWT and CSRF token on protected routes and resolves the current creator. Because the frontend and backend are on different domains in production, both cookies use `SameSite=None; Secure` and CORS allows credentials — these must agree or login fails only in production. Stateless tokens aren't revocable before expiry, so token lifetime is kept short (refresh tokens are future work).

---

## Deployment (Render + Vercel + Neon)

Build for this; deploying is a separate step from local development.

- **Database:** Neon (Postgres + pgvector). Confirm the extension is enabled via migration. Neon scales to zero when idle.
- **Backend:** Render, root directory `backend/`. The free tier sleeps and cold-starts (~30–60s wake-up). Combined with Neon scale-to-zero, the first request after idle hits two cold starts — the frontend shows an honest "waking up" state rather than a dead spinner. Do **not** add keep-alive ping hacks.
- **Frontend:** Vercel, root directory `frontend/`. `VITE_API_BASE_URL` is baked at **build time** — changing the backend URL requires a frontend rebuild, not just a restart.
- **CORS:** frontend (`*.vercel.app`) and backend (`*.onrender.com`) are different origins. FastAPI's CORS middleware must allow `FRONTEND_ORIGIN` (env var, so localhost works in dev). This is the most common "works locally, breaks deployed" bug — configure it early.
- **Secrets:** set backend secrets in Render, frontend build vars in Vercel. Anything `VITE_`-prefixed ships to the browser and must never hold a secret (LLM keys, OAuth client secret, and the encryption key live on the backend only).
- **Monorepo builds:** a push triggers both deploys; use each platform's build-filter / ignored-paths setting if you want frontend-only changes to skip the backend deploy and vice versa.

---

## Notes on scope

- **Processing is per-post and batch**, not real-time: the creator triggers a run ("process my latest post") or it runs on a schedule. No webhooks in v1.
- **Voice refresh** ("Refresh memory: past year") is an async, chunked, resumable, idempotent background job that pulls the last year of comments + the creator's replies, classifies and pairs them, embeds them, and writes to the voice store. On Render's free tier it runs as a FastAPI background task (no always-on worker); a real task queue is future work.
- **Positioning:** several tools already do AI comment replies (CommentShark, replient.ai, and others), and YouTube is adding native AI reply suggestions. This project's distinct angles are (1) a real internal MCP server as the tool layer, (2) bring-your-own-LLM-key rather than a bundled subscription, and (3) a platform-neutral core ready for additional platforms. For the price-sensitive Indian creator market specifically, BYO-key affordability plus voice fidelity on code-mixed (e.g. Hinglish/Tanglish) replies is the strongest differentiation.
- Future work (not in v1): auto-send beyond low-risk categories, additional platforms (Instagram/Facebook), the drafting and analytics agents, a real task queue, and a voice-correction learning loop using the draft→final-sent delta.
