# CLAUDE.md

This file guides Claude Code in building the project. Read it fully before writing any code. Work through the phases in order. Do not skip ahead — each phase depends on the one before it. At the end of each phase there is a checkpoint; stop, confirm it passes, then continue.

---

## What we are building

An in-app AI **comment agent** for content creators. The agent reads the comments on a creator's posts, classifies each comment into a category, and — based on a per-category policy the creator controls — either drafts a reply, auto-sends a low-risk reply, hides spam, or flags the comment for human review. Replies are written in the creator's own voice, learned from their past replies via semantic retrieval (RAG).

**v1 scope is deliberately narrow:**

- **YouTube only.** The architecture must hold space for other platforms (Instagram, etc.) later, but only YouTube is implemented now.
- **Comment agent only.** No drafting agent, no analytics agent, no DM/messaging agent. Those are future work.
- **Classify + draft-only replies first.** Auto-send for low-risk categories comes after the draft path is proven.

### Guiding principles (apply throughout)

1. **Platform-neutral core.** Only the platform-integration layer knows about YouTube. Everything above it operates on a normalized internal model. Adding a platform later must mean writing a new adapter, not changing the agent.
2. **LLM decides content; code decides control.** The LLM classifies comments and drafts replies. Deterministic code decides routing and whether anything is allowed to be auto-sent. Never let the model decide autonomy.
3. **Every write passes through one autonomy gate.** Posting, hiding, liking — all go through a single policy/autonomy check. Safety reasoning lives in one place.
4. **Safety floor that creators cannot override.** The `sensitive` category never auto-sends, regardless of policy. Any unconfigured category defaults to draft-and-approve, never auto-send.
5. **Audit + idempotency are first-class.** Every action is logged. Every write is guarded against double-execution. Build these in from the start, not as a retrofit.
6. **Secrets are sacred.** User LLM API keys and OAuth tokens are encrypted at rest. The encryption key lives only in the deployment env, never in the repo, DB, or logs. Never return a plaintext key to the frontend. Never log a key.

---

## Tech stack (fixed — do not substitute)

- **Backend:** Python, FastAPI
- **Frontend:** React + Vite + Tailwind CSS
- **Database:** PostgreSQL with the `pgvector` extension (semantic voice retrieval)
- **Migrations:** Alembic
- **Local DB:** Postgres in Docker (docker-compose), pgvector-enabled image
- **Deploy target:** backend on Render, frontend on Vercel, database on Neon (Postgres + pgvector). Build for this but do not deploy as part of this build.
- **LLM:** users bring their own provider/model and API key. There is no app-owned LLM subscription. Every agent LLM call uses the requesting creator's stored, encrypted key.

### Repo structure (monorepo)

```
/
  /backend          FastAPI app, Alembic migrations, all server code
  /frontend         React + Vite + Tailwind app
  docker-compose.yml  local Postgres + pgvector
  CLAUDE.md
  README.md
```

Both deploy from this one repo: Vercel root = `/frontend`, Render root = `/backend`.

### Environment variables (never hardcode; never commit real values)

Backend (Render / local `.env`, all server-side only):

- `DATABASE_URL` — local Docker URL in dev, Neon URL in prod
- `ENCRYPTION_KEY` — Fernet key; the crown jewel. Decrypts all user secrets.
- `JWT_SECRET` — signs/verifies auth JWTs; second crown-jewel secret. Env-only, never in repo/DB/logs.
- `FRONTEND_ORIGIN` — for CORS (localhost in dev, vercel domain in prod)
- `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET` — YouTube OAuth (server-side only)

Frontend (Vercel build-time, `VITE_`-prefixed, **shipped to the browser — never put a secret here**):

- `VITE_API_BASE_URL` — the backend URL

Provide a `.env.example` in each side listing variable names with empty/placeholder values. Never commit a real `.env`.

---

## Data model (the normalized store)

All tables use a surrogate primary key (UUID or bigserial) and `created_at` / `updated_at` timestamps. Platform-native IDs are stored as their own columns, never used as primary keys. Every table traces back to a `creator` so ownership checks are a clean join and no creator can ever read another's data. Encrypted values are stored as ciphertext blobs; the DB never decrypts — the app layer does.

**Category vocabulary** is a constrained, shared enum used identically by the classifier, the policy table, and the voice tags:
`spam`, `simple_positive`, `question`, `brand_inquiry`, `criticism`, `sensitive`, `other`

### Tables

- **creators** — app users. Identity.
- **platform_accounts** — one row per connected social account, belongs to a creator. Columns: platform (`youtube`), platform-native account id, encrypted OAuth access/refresh tokens, token expiry. This table is what makes multi-platform structural — many accounts per creator, each tagged by platform.
- **llm_credentials** — belongs to a creator. Chosen provider, chosen model, encrypted API key. Support rotate and hard-delete.
- **posts** — belongs to a platform_account. Platform-neutral fields + platform-native post id.
- **comments** — belongs to a post. Author, text, timestamp, platform-native comment id, `is_reply` (top-level vs reply), `authored_by_creator` (the creator's own replies are voice data), and the classification fields `category` and `confidence` (these MUST travel with the comment into the autonomy gate).
- **voice_examples** — belongs to a platform_account (voice is per-platform). Stores (parent comment text → creator reply text) pairs, tagged by category, with an `embedding` vector column (pgvector) for semantic retrieval. **These are their own rows (a denormalized copy of text + embedding), not foreign keys into `comments`**, because the voice store has a different lifecycle (rebuilt on refresh) than the live comment feed. Populated by the refresh-memory job; read by the drafting step.
- **category_policies** — belongs to a creator (per platform). One row per category: `action` (reply / hide / like / ignore / flag), `autonomy` (auto_send / draft / notify_only), optional `tone_constraint` text. The autonomy gate reads this on every write decision.
- **actions** — the approval queue AND the audit log AND the idempotency guard, in one table. One row per proposed/executed action: which comment, the **original drafted text** and the **final sent text** as separate fields (the creator's edits never overwrite the original draft — the delta is voice-correction signal), routing category, autonomy decision, status (pending / approved / sent / rejected / auto_sent / uncertain), timestamps, approver. A unique constraint prevents the same comment being actioned twice.

The schema is your first Alembic migration. Enabling the `pgvector` extension is an early migration step so local and prod stay consistent.

### Concrete schema (target DDL — UUID primary keys)

This is the authoritative shape for the Phase 1 migration. Primary keys are UUIDs (`gen_random_uuid()`); every table has `created_at` and `updated_at` (timestamptz, default now). Platform-native ids are their own columns, never primary keys. All foreign keys are `ON DELETE CASCADE` from their owning parent unless noted. Encrypted columns are `bytea` holding Fernet ciphertext.

```
-- extension (early migration, before tables)
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- for gen_random_uuid()

-- shared category vocabulary
CREATE TYPE comment_category AS ENUM
  ('spam','simple_positive','question','brand_inquiry','criticism','sensitive','other');

CREATE TYPE policy_action  AS ENUM ('reply','hide','like','ignore','flag');
CREATE TYPE autonomy_level AS ENUM ('auto_send','draft','notify_only');
CREATE TYPE action_status  AS ENUM ('pending','approved','sent','rejected','auto_sent','uncertain','failed');
CREATE TYPE platform_kind  AS ENUM ('youtube');   -- extended as platforms are added

creators (
  id            uuid PK default gen_random_uuid(),
  email         text UNIQUE NOT NULL,
  display_name  text,
  password_hash text NOT NULL,                  -- bcrypt/argon2 hash; plaintext never stored/logged
  created_at, updated_at
)
-- No sessions table: auth is stateless JWT. The token is signed (JWT_SECRET) and self-contained.

platform_accounts (
  id                   uuid PK,
  creator_id           uuid FK -> creators(id) NOT NULL,
  platform             platform_kind NOT NULL,
  platform_account_id  text NOT NULL,            -- native channel/account id
  access_token_enc     bytea NOT NULL,           -- encrypted
  refresh_token_enc    bytea,                    -- encrypted
  token_expires_at     timestamptz,
  status               text NOT NULL default 'connected',  -- connected | needs_reauth
  created_at, updated_at,
  UNIQUE (platform, platform_account_id)
)

llm_credentials (
  id            uuid PK,
  creator_id    uuid FK -> creators(id) NOT NULL,
  provider      text NOT NULL,                   -- e.g. 'anthropic','openai'
  model         text NOT NULL,
  api_key_enc   bytea NOT NULL,                  -- encrypted
  key_hint      text NOT NULL,                   -- masked display, e.g. 'sk-...4f2a'
  created_at, updated_at
)

posts (
  id                 uuid PK,
  platform_account_id uuid FK -> platform_accounts(id) NOT NULL,
  platform_post_id   text NOT NULL,              -- native video id
  title              text,
  url                text,
  published_at       timestamptz,
  created_at, updated_at,
  UNIQUE (platform_account_id, platform_post_id)
)

comments (
  id                  uuid PK,
  post_id             uuid FK -> posts(id) NOT NULL,
  platform_comment_id text NOT NULL,             -- native comment id
  parent_comment_id   uuid FK -> comments(id),   -- null = top-level
  author_name         text,
  author_channel_id   text,
  text                text NOT NULL,
  is_reply            boolean NOT NULL default false,
  authored_by_creator boolean NOT NULL default false,  -- creator's own replies = voice data
  published_at        timestamptz,
  category            comment_category,          -- null until classified
  confidence          real,                      -- 0..1, travels into the autonomy gate
  classified_at       timestamptz,
  created_at, updated_at,
  UNIQUE (post_id, platform_comment_id)
)

voice_examples (                                 -- own rows, NOT FK into comments
  id                  uuid PK,
  platform_account_id uuid FK -> platform_accounts(id) NOT NULL,  -- voice is per-platform
  category            comment_category NOT NULL,
  parent_comment_text text NOT NULL,
  reply_text          text NOT NULL,
  embedding           vector(1536),              -- dim must match the embedding model in use
  source_published_at timestamptz,               -- for recency-weighted retrieval
  created_at, updated_at,
  UNIQUE (platform_account_id, category, parent_comment_text, reply_text)  -- idempotent refresh
)
-- ANN index, added after initial bulk load:
-- CREATE INDEX ON voice_examples USING hnsw (embedding vector_cosine_ops);

category_policies (
  id              uuid PK,
  creator_id      uuid FK -> creators(id) NOT NULL,
  platform        platform_kind NOT NULL,
  category        comment_category NOT NULL,
  action          policy_action NOT NULL,
  autonomy        autonomy_level NOT NULL,
  tone_constraint text,                          -- optional, fed into draft prompt
  created_at, updated_at,
  UNIQUE (creator_id, platform, category)
)

actions (
  id                 uuid PK,
  comment_id         uuid FK -> comments(id) NOT NULL,
  creator_id         uuid FK -> creators(id) NOT NULL,
  category           comment_category NOT NULL,  -- routing category at decision time
  autonomy_decision  autonomy_level NOT NULL,
  draft_text         text,                       -- original LLM draft (preserved)
  final_text         text,                       -- what was actually sent (may differ if edited)
  status             action_status NOT NULL default 'pending',
  platform_reply_id  text,                       -- native id of the posted reply, once sent
  approver           text,                       -- who approved (creator id / 'auto')
  error_detail       text,                       -- scrubbed, never contains secrets
  sent_at            timestamptz,
  created_at, updated_at,
  UNIQUE (comment_id)                            -- idempotency: one action per comment
)
```

Notes: `embedding` dimension must match whatever embedding model the voice pipeline uses — fix it once and keep it consistent. The `actions.UNIQUE(comment_id)` constraint is the idempotency backstop; the orchestration also checks for an existing action before writing. Alembic autogenerate does not reliably emit `pgvector` columns/indexes or enums — review and hand-edit the generated migration.

---

## Architecture (layers, bottom to top)

1. **Platform integration layer** — the only YouTube-aware code. OAuth handling, reading comment threads, (later) posting/hiding/liking. Translates YouTube's API shapes into the normalized model. Nothing above this layer references YouTube.
2. **Normalized data store** — Postgres, the tables above. Platform-neutral.
3. **Tool layer (internal MCP server)** — capabilities exposed as tools: `read_comments`, `classify_comments`, `get_voice_examples`, `draft_reply`, and the write tools `post_reply` / `hide_comment` / `like_comment`. Implement this as a real MCP server with the in-app agent as the MCP client (use an official MCP SDK). Tools are capability primitives only — they do NOT contain autonomy logic.
4. **Agent / orchestration layer** — the comment-agent loop: pull comments → classify batch → for each, look up policy → execute per policy. This layer is stateless and idempotent; all state lives in the DB. The LLM is used only for classify and draft; routing is deterministic code here.
5. **Policy / autonomy gate** — every write passes through here. Reads `category_policies`, enforces the safety floor (`sensitive` never auto-sends; unconfigured → draft), uses `category` + `confidence` to decide auto_send vs draft. Centralized.
6. **Creator-facing surface (frontend)** — approval queue, bucket view with example comments, policy editor, audit log, account/LLM-key settings.

### Processing model

**Batch, not real-time.** The creator triggers a run ("process my latest post") or it runs on a schedule. No webhooks/polling infra in v1.

### Voice pipeline (separate from the live loop)

A "Refresh memory (past year)" button calls an endpoint that kicks off an **async** job: pull the last year's comments + the creator's replies, classify the parent comments, pair them into (comment → reply) examples tagged by category, embed them, write to `voice_examples`. Must be: async (button starts job, UI shows progress), idempotent (re-running does not duplicate), and incremental-friendly (later refreshes pull only new comments since last run). On Render's free tier there is no always-on worker, so implement as a FastAPI background task and CHUNK the work so no single request runs long enough to be killed. (A real task queue is explicitly v2.)

---

## API contract (FastAPI)

REST, JSON, all under `/api`. Every endpoint except auth requires the authenticated creator; every resource access is scoped to that creator (ownership enforced server-side — never trust an id from the client without checking it belongs to the caller). Errors use the shared structured shape (`{ "error": { "code": ..., "message": ... } }`), correct status codes, never a stack trace or secret in the body. List endpoints paginate.

This is the v1 surface — build endpoints as their phase is reached, not all upfront.

```
Auth / session  (Phase 2)
  POST   /api/auth/signup   {email, password, display_name} -> hash password, create creator
  POST   /api/auth/login    {email, password} -> verify hash; set JWT in HttpOnly cookie + CSRF token cookie
  POST   /api/auth/logout   clear both cookies (stateless: token expiry is the real bound)
  GET    /api/me            current creator (resolved from the JWT by the auth middleware)
  -- login/signup are exempt from the JWT check; all other routes go through the auth middleware.

LLM credentials  (Phase 2)
  GET    /api/llm-credentials             list (masked only — never plaintext)
  POST   /api/llm-credentials             add {provider, model, api_key} -> validates, encrypts, stores; returns masked
  PUT    /api/llm-credentials/{id}        rotate key / change model
  DELETE /api/llm-credentials/{id}        hard delete

Platform accounts  (Phase 3)
  GET    /api/platform/youtube/connect    begin YouTube OAuth (redirect)
  GET    /api/platform/youtube/callback   OAuth callback -> stores encrypted tokens
  GET    /api/platform-accounts           list connected accounts + status
  DELETE /api/platform-accounts/{id}      disconnect

Posts & comments  (Phase 3 / 5)
  GET    /api/posts                        list the creator's pulled posts
  POST   /api/posts/{id}/sync              pull/refresh this post's comments from the platform
  GET    /api/posts/{id}/comments          list comments for a post (filter by ?category=)
  POST   /api/posts/{id}/classify          classify this post's comments (batch) -> buckets
  GET    /api/posts/{id}/buckets           category counts + sample comments for the bucket view

Voice  (Phase 6)
  POST   /api/voice/refresh                start async refresh job {timeframe:'past_year'} -> {job_id}
  GET    /api/voice/refresh/{job_id}       job status/progress (ingested/total/skipped)
  GET    /api/voice/examples               list stored examples (filter by ?category=)

Policies  (Phase 8)
  GET    /api/policies                     current per-category policy table
  PUT    /api/policies                     upsert policy entries (validated: known categories,
                                           fixed autonomy values; safety floor enforced server-side
                                           — sensitive can never be set to auto_send)

Drafts / actions / queue  (Phase 7 / 9)
  POST   /api/posts/{id}/run               run the agent loop on a post: classify -> route ->
                                           draft for reply-categories -> queue (per policy)
  GET    /api/actions                       approval queue (?status=pending) and history
  GET    /api/actions/{id}                  single action (draft + final + status)
  POST   /api/actions/{id}/send             send as-is (posts via gate + idempotency check)
  POST   /api/actions/{id}/edit-send        {final_text} -> store edit separately, then send
  POST   /api/actions/{id}/reject           reject/skip a draft
  POST   /api/actions/{id}/undo             undo within the undo window (for auto_sent)
```

Behavioral rules the endpoints must honor: the send endpoints go through the central autonomy gate and check `actions` for an existing send before posting (never blind-retry; mark `uncertain` on ambiguous failures). `edit-send` preserves `draft_text` and writes the creator's text to `final_text`. `/run` drafts only for categories whose policy action is `reply`. `PUT /api/policies` rejects any attempt to set `sensitive` to `auto_send` regardless of payload.

---

## Frontend design system

The subject is a creator taming a noisy, overwhelming comment section into a calm, controllable rhythm of review-and-send — while their _voice_ is being protected and every automated action stays legible. The interface should feel like a **calm control room**, not another loud social dashboard. The bucketed comments and the approval queue are the hero; restraint everywhere else. Avoid the generic-SaaS look (blue primary, card grid, drop shadows everywhere) and avoid the three AI-default aesthetics (cream+serif+terracotta; near-black+acid accent; broadsheet hairline columns).

**Concept:** a quiet, focused workspace where state and autonomy are always readable at a glance — what's pending, what was drafted vs. edited, what's auto vs. human, what's flagged for care.

### Tokens (Tailwind theme extension — define once, derive everything from these)

Color — a calm, low-chroma base with category-coded accents that carry meaning (color encodes the autonomy/risk state, it is not decoration):

```
--bg            #0F1115   near-black slate, the control-room ground
--surface       #171A21   raised panels / queue cards
--surface-2     #1F232C   inset / hover
--border        #2A2F3A   hairline separators
--text          #E7EAEF   primary text
--text-muted    #9AA3B2   labels, captions, metadata
--accent        #6E8BFF   primary action (send/approve) — one calm indigo, used sparingly
```

Category / state accents (semantic — reused in bucket view, queue, audit log so a color always means the same thing):

```
--cat-positive  #3FB6A8   simple_positive
--cat-question  #6E8BFF   question
--cat-brand     #C9A24B   brand_inquiry
--cat-criticism #D98A3D   criticism
--cat-sensitive #E0566B   sensitive (also the "never auto" signal)
--cat-spam      #6B7280   spam (muted/dimmed)
--cat-other     #8A93A3   other
```

Autonomy state must be visually distinct at a glance: `auto_send` (a filled accent chip), `draft` (an outline chip), `notify_only`/`flag` (a quiet dot). Sensitive items always carry the `--cat-sensitive` marker and never show an auto affordance.

Typography — pick a pairing that is calm and precise, not the usual Inter-for-everything:

```
display/UI headings :  "Space Grotesk"  (geometric, a little character, used at large sizes only)
body / UI text      :  "Inter"          (workhorse, but not the display face)
data / metadata     :  "IBM Plex Mono"  (counts, ids, timestamps, confidence values)
```

Use the mono face for everything numeric/system (comment counts, confidence scores, timestamps, the masked key) — it reinforces the "control room" read and separates data from prose. Set a clear scale; weights deliberate (display 500–600, body 400, mono 400).

Layout & shape:

```
radius     : 8px on panels/cards, 6px on controls — soft but not pill-round
spacing    : generous; an 8px base unit; let panels breathe
density    : the queue is scannable — one action per row, comment text the focus,
             metadata (category chip, autonomy chip, confidence) aligned right
borders    : hairline (1px --border); prefer separation by border + spacing over heavy shadows
shadow     : at most one soft elevation for the active/focused card; no shadow soup
```

Signature element: the **autonomy chip + confidence read** on every comment/action — a small, consistent marker that always tells the creator, at a glance, what will happen (auto vs. draft vs. flag) and how sure the classifier was. It's the one memorable, repeated device, and it makes the safety model visible. Keep everything else quiet so this reads.

Copy (from the user's side of the screen, active voice, consistent verbs):

- Actions say what happens: "Send", "Edit & send", "Skip", "Approve" — and the same verb carries through to the resulting toast ("Sent", "Skipped").
- Name things by what the creator controls: "Comments", "Replies", "Voice", "Rules" (not "classifier", "policy engine", "RAG store").
- Empty states invite action: an unconnected account → "Connect YouTube to pull your comments." An empty queue → "Nothing waiting — run the agent on a post to draft replies."
- Errors are plain and actionable, in the interface's voice, never apologetic or vague: "Your API key was rejected — update it in Settings." / "Couldn't post this reply. Retry?" / "YouTube needs you to reconnect."

Quality floor (build to it without announcing it): responsive down to mobile, visible keyboard focus states, `prefers-reduced-motion` respected, sufficient contrast on all text. Motion is minimal and purposeful — a gentle reveal when drafts land in the queue, nothing ambient or decorative.

---

## Security requirements (non-negotiable)

- Encrypt user LLM API keys and OAuth tokens **at rest** using `cryptography` Fernet. Encrypt before write, decrypt only in memory at point of use.
- `ENCRYPTION_KEY` lives only in the deployment env / local `.env`. Never in the repo, never in the DB, never in a log. It is the crown jewel — its leak equals every user key leaking.
- Never return a full plaintext key to the frontend. After save, the UI only ever sees a partial mask (e.g. `sk-...4f2a`).
- Scrub keys from ALL logs, error messages, and tracebacks — especially around LLM calls, where a naive error handler will log the request payload containing the key.
- Enforce per-user ownership on every decrypt path. A run for creator A must never decrypt creator B's key.
- Validate a key with a cheap test call before storing. Support rotate and hard-delete.
- HTTPS in transit (free from Render/Vercel). Configure FastAPI CORS to allow only `FRONTEND_ORIGIN`.
- In the UI, advise users to set a spending cap on their provider account so a worst-case leak is bounded.

### Authentication & sessions

- Hash passwords with a slow, salted, purpose-built hash (**bcrypt or Argon2** via `passlib`) — never a fast general-purpose hash. Store only the hash in `creators.password_hash`. Plaintext passwords are never stored and never logged. Enforce a minimum password length on signup.
- **Auth is stateless JWT.** On login, issue a signed JWT (HS256, signed with `JWT_SECRET`) carrying the creator id and an expiry. No server-side session store. `JWT_SECRET` is a second crown-jewel secret alongside `ENCRYPTION_KEY` — env-only, never in repo/DB/logs.
- **The JWT lives in an `HttpOnly`, `Secure`, `SameSite` cookie** (not JS-readable, so it's protected from XSS theft). Because it's a cookie the browser sends automatically, it is CSRF-exposed — so:
- **CSRF protection via double-submit token.** On login also set a SEPARATE, **non-HttpOnly** CSRF cookie (random token). The frontend reads it and echoes it in an `X-CSRF-Token` header on every state-changing request (POST/PUT/DELETE). The backend verifies header == cookie. A cross-site forgery can ride the JWT cookie but cannot read the CSRF cookie to set the matching header, so it's blocked. `SameSite` on the cookies is the second layer.
- **Stateless trade-off (accepted):** a JWT cannot be revoked before expiry — logout only clears the client cookies; the token stays valid until it expires. Mitigate with a **short token lifetime** (15–30 min). Refresh tokens (short access + longer refresh) are the documented next step, not v1.
- **Auth middleware** on all protected routes (one dependency, built before any protected endpoint): (1) read JWT from cookie, (2) verify signature + expiry → 401 if absent/invalid/expired, (3) on state-changing methods verify `X-CSRF-Token` header matches the CSRF cookie → 403 on mismatch, (4) resolve and attach the current creator. This resolved creator is the "authenticated creator" every creator-scoped ownership check depends on. `login`/`signup` are exempt from step 1–2.
- Login hardening: rate-limit login; return the **same** generic "invalid email or password" for unknown-email and wrong-password (no account enumeration).
- Cross-domain note: frontend (vercel.app) and backend (onrender.com) are different sites, so both cookies need `SameSite=None; Secure` and CORS must allow credentials (`allow_credentials=True`, explicit origin, not `*`). Cookie + CORS settings must agree or login works locally but fails in production.
- Password reset (email-based) is explicitly **out of v1 scope**.

---

## Deployment seams to respect while building (build for these; do not deploy now)

- Monorepo, two deploy targets: set Vercel root to `/frontend`, Render root to `/backend`.
- Render free tier sleeps and cold-starts; Neon scales to zero. Show an honest "waking up" state in the UI rather than a dead spinner. Do not add keep-alive ping hacks.
- CORS: frontend (vercel.app) and backend (onrender.com) are different origins — configure FastAPI CORS middleware against `FRONTEND_ORIGIN` (env var, so localhost works in dev), and with `allow_credentials=True` so the session cookie is sent. This is the #1 "works locally, breaks deployed" bug — set it up early. It must agree with the `SameSite=None; Secure` session cookie (see Authentication).
- Vite bakes `VITE_API_BASE_URL` at build time; changing the backend URL requires a frontend rebuild.
- Alembic migrations run as a deliberate one-off against Neon, not automatically on every Render boot.
- pgvector must be enabled on Neon via an early migration.

---

## Error handling (non-negotiable — applies to every phase)

Error handling here is not generic, because the cost of a failure depends on what failed. A failed read is harmless. A failed **write** to a platform is dangerous — you must know whether it actually posted before retrying, or you double-post in the creator's name. A failed LLM call costs the creator money and risks leaking their key. Handle failures per-layer, in the way that is safe for that layer's blast radius. Do NOT wrap everything in one global try/catch.

### Two absolute rules

1. **Never swallow an error silently.** Every caught error is either handled meaningfully or logged and surfaced. A bare `except: pass` (or an empty catch) is banned.
2. **Never log a secret in any error path.** The LLM and OAuth error handlers must scrub the payload (API keys, tokens) BEFORE logging. A naive handler that logs the failed request will leak the key — this is the most common leak path.

### External API calls (YouTube, the user's LLM)

- Classify failures into distinct kinds and handle each differently:
  - **Auth failures** (expired/revoked token, rejected API key): do NOT retry. Surface a clear, actionable message ("reconnect your YouTube account" / "your API key was rejected"). Mark the credential/account as needing attention.
  - **Rate limits / transient 5xx / network timeouts:** retry with exponential backoff, capped (e.g. 3 attempts). Then fail gracefully.
  - **YouTube `quotaExceeded`:** detect specifically. Do NOT retry — it is pointless until quota reset. Pause the run and report honestly that the quota is exhausted.
- All retries are bounded. No unbounded retry loops anywhere.

### Write actions (post_reply, hide, like) — idempotency over retries

- Before any write, check `actions`: has this comment already been actioned? Use the table's logic first; the unique constraint is the backstop.
- If a write times out **ambiguously** (you cannot tell whether it landed): do NOT blind-retry. Mark the action `uncertain`, then reconcile (re-read the comment's replies) or surface it to the creator to confirm. **Double-posting in the creator's name is worse than a failed post — when in doubt, stop, do not retry.**

### Async voice-refresh job — partial failure & resumability

- Process in chunks. A failing chunk must not lose completed work — record progress so the job is **resumable**.
- Skip-and-log individual bad records (one un-embeddable comment) rather than aborting the whole job.
- Stay **idempotent** — a re-run never duplicates examples.
- Report real status to the creator ("ingested 1,840 of 2,100, 3 skipped"), never a silent hang or a misleading "done."

### Classifier & drafting — graceful degradation, never crash

- If the LLM returns an unparseable or low-confidence result, fall back to the SAFE path: route to `other` / draft-and-approve. Never guess and never auto-send on a degraded result.
- A malformed classifier response must never cascade into a wrong autonomy decision. Uncertainty always resolves to "ask the human," which is both the safe behavior and the safe error behavior.

### API boundary (FastAPI)

- A global exception handler catches anything unhandled, returns a generic 500 with NO stack trace, internal detail, or secret in the response body, and logs the real (scrubbed) detail server-side.
- Use consistent structured error responses (a code + a human-readable message) and correct HTTP status codes.

### Frontend — distinguish "waking up" vs "broken" vs "your action failed"

- A slow first response (Render/Neon cold start) is NOT an error — show a "waking up" state, not an error toast.
- User-facing errors must be actionable ("reconnect YouTube", "your API key was rejected", "this reply couldn't be posted — retry?"), never a raw 500.
- An action that failed mid-flight must leave the approval queue in a clear state so the creator knows what did and did not happen.

---

## Phased build plan (target: one day, in order)

Each phase ends with a checkpoint. Do not start a phase until the previous checkpoint passes. Prefer small, working increments over large untested ones.

### Phase 0 — Skeleton & local infra

- Create the monorepo structure (`/backend`, `/frontend`, `docker-compose.yml`).
- `docker-compose.yml` with a pgvector-enabled Postgres image; confirm it boots.
- FastAPI app that starts and serves a `/health` endpoint.
- React + Vite + Tailwind app that builds and renders a placeholder page.
- Initialize Alembic, pointed at `DATABASE_URL`. Add `.env.example` files. Add `.gitignore` (exclude `.env`, `node_modules`, `__pycache__`, etc.).
- **Checkpoint:** `docker compose up` runs Postgres; backend `/health` returns OK; frontend dev server renders; `alembic upgrade head` runs cleanly (even with no tables yet).

### Phase 1 — Schema & normalized store

- Write the first migration: enable `pgvector`, define the category enum, and create all tables from the data model above with correct relationships, surrogate keys, timestamps, ownership FKs, the `voice_examples.embedding` vector column, and the uniqueness constraint on `actions` for idempotency.
- Add SQLAlchemy models matching the schema.
- **Checkpoint:** migration applies cleanly to the Docker DB; models import; a smoke test can insert and read a creator and a platform_account.

### Phase 2 — Security foundation (auth first, then secrets)

- **Authentication first** — everything creator-scoped depends on it. Implement signup (hash password with bcrypt/argon2), login (verify password; issue a short-lived signed JWT in an HttpOnly/Secure/SameSite cookie + set a non-HttpOnly CSRF cookie), logout (clear both cookies). Build the **auth middleware** used by every protected route: verify the JWT (401 if absent/invalid/expired), verify the `X-CSRF-Token` header matches the CSRF cookie on state-changing methods (403 on mismatch), and resolve/attach the current creator. Apply login hardening (rate limit, generic error, no account enumeration).
- Implement the Fernet encrypt/decrypt utility reading `ENCRYPTION_KEY` from env.
- Implement `llm_credentials` storage: validate-on-entry (cheap test call), encrypt, store; endpoints to save, rotate, hard-delete; return only the masked form.
- Add log scrubbing so keys AND passwords never appear in logs/tracebacks.
- Establish the error-handling baseline here: the FastAPI global exception handler (generic 500, no leaked internals), the structured error-response shape, and the secret-scrubbing log wrapper used around all LLM/OAuth calls.
- **Checkpoint:** sign up, log in (JWT cookie + CSRF cookie set), hit a protected GET with the JWT cookie (succeeds; 401 without it), make a state-changing POST and confirm it's rejected 403 without a matching `X-CSRF-Token` header and succeeds with it, log out and confirm both cookies are cleared; password hash is bcrypt/argon2 in the DB with no plaintext anywhere; can store and retrieve a key round-trip (encrypted in DB, decrypts in memory, never returned plaintext to client); deliberately trigger an error on an LLM call and confirm neither the key nor any password is in the logs and the API response contains no internal detail.

### Phase 3 — YouTube integration layer

- OAuth flow to connect a YouTube account; store encrypted tokens in `platform_accounts`; refresh handling.
- Read comment threads for a creator's post; translate into normalized `comments` (including `is_reply` and `authored_by_creator`). Persist posts and comments.
- Keep ALL YouTube specifics inside this layer; everything it returns is normalized.
- Handle YouTube failures per the error policy: auth failures (expired/revoked token) surface "reconnect" and are not retried; transient errors retry with capped backoff; `quotaExceeded` is detected specifically and pauses rather than retrying.
- **Checkpoint:** connect a real YouTube account, pull a post's comments, see normalized rows in the DB with no YouTube-shaped data leaking upward; revoke/expire a token and confirm it surfaces a clean "reconnect" message rather than a crash or retry storm.

### Phase 4 — Internal MCP tool layer

- Stand up the MCP server (official SDK) exposing read-only tools first: `read_comments`, `classify_comments`, `get_voice_examples`. Then the content tool `draft_reply`. Write tools (`post_reply`, `hide_comment`, `like_comment`) are defined but NOT wired to auto-execution yet.
- The in-app agent is the MCP client. Tools are capability primitives — no autonomy logic inside them.
- **Checkpoint:** the agent (client) can call each read tool through the MCP server and get normalized results.

### Phase 5 — Classifier (read-only, the load-bearing piece)

- Implement `classify_comments`: batch-classify a post's comments into the category enum, returning `{category, confidence}` per comment, persisted onto the comment rows. Bias toward caution: low confidence or any signal of `sensitive`/`criticism`/`brand_inquiry` routes conservatively.
- Frontend: a bucket view showing a post's comments sorted by category with counts and example comments.
- Apply graceful degradation: an unparseable or low-confidence LLM result falls back to `other` / conservative routing — never a crash, never a confident wrong label.
- **Checkpoint:** run the classifier on a real post's comments; buckets and confidence look sensible; the bucket view renders them; feed it a deliberately malformed/empty response and confirm it degrades to the safe path instead of crashing. This is demoable on its own with zero write scopes.

### Phase 6 — Voice store & refresh-memory pipeline

- Implement the async "Refresh memory (past year)" endpoint + background task: pull last year's comments + creator replies, classify parents, pair into (comment → reply) examples, embed, write to `voice_examples`. Idempotent, chunked, incremental-friendly. Curate: prefer recent and higher-engagement replies; filter very short/low-effort ones.
- Frontend: a settings page with the refresh button and a progress indicator.
- Apply the partial-failure policy: chunked and resumable (a failed chunk loses no completed work), skip-and-log bad records, idempotent re-runs, honest progress/status reporting.
- **Checkpoint:** clicking refresh populates `voice_examples` with embeddings; re-running does not duplicate; pgvector similarity query returns same-category examples for a sample comment; killing the job mid-run and restarting resumes without duplicating or losing completed work.

### Phase 7 — Draft generation (voice-aware, draft-only)

- Implement `draft_reply`: given a comment + its category, retrieve same-category voice examples by semantic similarity (pgvector), plus the optional per-category `tone_constraint`, and draft a reply in the creator's voice. Everything is draft-only at this phase.
- **Generate drafts only for comments whose policy action is `reply`** — not for every comment. Spam/ignore/hide categories get no draft. This keeps LLM cost down (no calls on comments the creator would never answer) and keeps the queue free of junk.
- Write proposed drafts into `actions` as `pending`, storing the **original drafted text** in its own field. When the creator later edits before sending, the final sent text is stored separately so the original draft is preserved (see Phase 9) — the draft→final delta is the highest-quality voice-correction signal and must not be discarded, even though the learning loop itself is future work.
- **Checkpoint:** for a question/substantive comment, the agent produces a drafted reply that reflects the creator's voice; the draft lands in the `actions` queue as `pending`; comments in non-reply categories produce no draft.

### Phase 8 — Policy table & autonomy gate

- Implement `category_policies` CRUD. Frontend policy editor where the creator, after seeing the classified buckets, sets per-category `action` + `autonomy` + optional `tone_constraint` through a VALIDATED UI (known categories, fixed autonomy values).
- Implement the central autonomy gate every write passes through: reads policy, enforces the safety floor (`sensitive` never auto-sends; unconfigured → draft), uses `category` + `confidence` for the auto_send-vs-draft decision.
- Wire the orchestration loop: pull → classify → per-category policy lookup → produce drafts / flag / queue.
- **Checkpoint:** with a configured policy table, a run routes each comment correctly into the `actions` queue with the right autonomy decision; the gate refuses to mark `sensitive` as auto-send even if asked.

### Phase 9 — Approval queue, audit log, and gated send

- Frontend approval queue reading `pending` actions, grouped per post. For each drafted reply the creator can: **view** it, **send** it as-is, or **edit then send**. Reject/skip is also available.
  - **Send as-is:** posts the draft via the write tool through the autonomy gate.
  - **Edit then send:** the creator's edited text is saved as the **final sent text in a separate field** (the original draft is preserved, never overwritten), then posted through the gate.
- Audit log view of executed actions, showing the original draft and the final sent text side by side where they differ.
- Enable auto_send ONLY for the narrow low-risk categories per policy, with an undo window and full audit logging. Idempotency enforced via the `actions` unique constraint (check first, constraint as backstop; never blind-retry an ambiguous send — mark `uncertain` and reconcile).
- Vary auto-sent low-risk replies (small set of voiced variations) so they never read as a canned bot.
- **Checkpoint:** view a drafted reply → send as-is → it posts to YouTube and appears in the audit log; edit a different draft → send → the edited text posts and BOTH original draft and final text are stored; re-running the loop does not double-post; a low-risk auto_send posts with variation and is logged with an undo path.

### Phase 10 — Polish & deploy-readiness (do not deploy)

- CORS configured against `FRONTEND_ORIGIN`; `VITE_API_BASE_URL` wired; "waking up" UI state for cold starts.
- `.env.example` complete on both sides; README with local-run instructions and a note on the Render/Vercel/Neon deploy seams.
- Confirm no secret is ever sent to the frontend or written to a log.
- **Checkpoint:** fresh clone + `docker compose up` + documented steps brings the whole app up locally end-to-end; security review of logs and frontend payloads passes.

---

## How to work

- Follow the phase order. Stop at each checkpoint and verify before continuing.
- Keep YouTube-specific code confined to the integration layer at all times.
- Keep control logic (routing, autonomy) in deterministic code; keep the LLM to classify and draft.
- Write the env-var and secret handling correctly from Phase 2 — do not defer security.
- Prefer small working commits per phase. If a checkpoint fails, fix before moving on.
- When a decision is ambiguous, choose the option that (a) keeps the core platform-neutral and (b) errs toward draft/flag over auto-send.
