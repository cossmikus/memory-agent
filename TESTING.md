# Setup & Testing Guide

Step-by-step instructions to run the memory service in Docker and exercise every endpoint. Anyone (you, a reviewer, a teammate) should be able to copy-paste their way through this.

## Prerequisites

- **Docker Desktop** running (`docker --version` should print 24.0+, `docker compose version` should print v2+).
- **OpenAI API key** (starts with `sk-proj-...`). Get one at https://platform.openai.com/api-keys.
- **`curl`** and **`jq`** in your shell. `jq` is optional but makes output readable.
  - macOS: `brew install jq`
  - Linux: `apt install jq` or `dnf install jq`

## Step 1 — Configure your API key

From the project root:

```bash
cp .env.example .env
```

Open `.env` in your editor and replace `sk-proj-...` with your real key:

```bash
OPENAI_API_KEY=sk-proj-YOUR-REAL-KEY-HERE
```

Leave the other defaults alone unless you want to customize. **Do not commit `.env`** — it's gitignored.

## Step 2 — Build the image

```bash
docker compose build
```

First build takes ~60 seconds (downloading Python deps). Subsequent builds are cached.

## Step 3 — Start the service

```bash
docker compose up -d
```

The `-d` flag runs it in the background.

You should see:

```
✔ Network agent_challenge_default Created
✔ Container memory-service        Started
```

## Step 4 — Wait until it's healthy

```bash
until curl -sf http://localhost:8080/health; do sleep 1; done
```

The loop exits as soon as `/health` returns 200. Then check the body:

```bash
curl -s http://localhost:8080/health | jq
```

Expected:

```json
{
  "status": "ok",
  "embedding_available": true,
  "llm_available": true,
  "version": "0.1.0"
}
```

`embedding_available` and `llm_available` should both be `true` — that means your OpenAI key was picked up. If they're `false`, the service still runs (regex fallback) but extraction quality will be poor.

## Step 5 — Ingest a turn (`POST /turns`)

Tell the service about a conversation:

```bash
curl -X POST http://localhost:8080/turns \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "s1",
    "user_id": "alice",
    "messages": [
      {"role": "user", "content": "I work at Stripe as a backend engineer. I live in Berlin and I have a dog named Biscuit."},
      {"role": "assistant", "content": "Nice to meet you, Alice!"}
    ],
    "timestamp": "2025-03-15T10:30:00Z",
    "metadata": {}
  }' | jq
```

Expected (the `id` will differ):

```json
{ "id": "a3f4b9c2-..." }
```

This took 5–10 seconds because the service called GPT-4o-mini to extract structured facts. By the time you see the response, the memory is fully indexed and queryable.

## Step 6 — Inspect what got extracted (`GET /users/{user_id}/memories`)

```bash
curl -s http://localhost:8080/users/alice/memories | jq
```

Expected: structured, typed memories. You should see entries like:

```json
{
  "memories": [
    {
      "id": "...",
      "type": "fact",
      "key": "employer",
      "value": "Stripe",
      "confidence": 0.95,
      "active": true,
      "supersedes": null,
      ...
    },
    {
      "type": "fact",
      "key": "job_title",
      "value": "backend engineer",
      ...
    },
    {
      "type": "fact",
      "key": "location_city",
      "value": "Berlin",
      ...
    },
    {
      "type": "fact",
      "key": "pet:Biscuit:species",
      "value": "dog",
      ...
    },
    {
      "type": "fact",
      "key": "pet:Biscuit:name",
      "value": "Biscuit",
      ...
    }
  ]
}
```

**Key thing to notice:** the response is _structured memories_, not raw message text. This is what makes it a memory service, not a message log.

## Step 7 — Recall context (`POST /recall`) — cross-session

The whole point of the service: in a brand-new session, the agent asks "what should I know?" and gets relevant context.

```bash
curl -X POST http://localhost:8080/recall \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "Where does this user work and where do they live?",
    "session_id": "s2",
    "user_id": "alice",
    "max_tokens": 512
  }' | jq
```

Expected:

```json
{
  "context": "## Known facts about this user\n- Employer: Stripe\n- Job Title: backend engineer\n- Location City: Berlin\n- Pet Biscuit Name: Biscuit\n...",
  "citations": [
    { "turn_id": "...", "score": 0.5..., "snippet": "..." }
  ]
}
```

Note that `session_id` is `s2` (a different session from where we wrote the memory). The service correctly accumulates knowledge across sessions for the same user.

## Step 8 — Test fact evolution (Stripe → Notion)

Ingest a second turn where Alice changes jobs:

```bash
curl -X POST http://localhost:8080/turns \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "s-new-job",
    "user_id": "alice",
    "messages": [
      {"role": "user", "content": "Big update — I just started a new job at Notion."}
    ],
    "timestamp": "2025-05-01T09:00:00Z",
    "metadata": {}
  }' | jq
```

Now check the memories — the employer chain should show the supersession:

```bash
curl -s http://localhost:8080/users/alice/memories | jq '[.memories[] | select(.key == "employer")]'
```

Expected:

```json
[
  {
    "key": "employer",
    "value": "Stripe",
    "active": false,
    "supersedes": null,
    ...
  },
  {
    "key": "employer",
    "value": "Notion",
    "active": true,
    "supersedes": "<the-Stripe-row-id>",
    ...
  }
]
```

And the recall must surface the **current** employer:

```bash
curl -X POST http://localhost:8080/recall \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "Where does this user currently work?",
    "session_id": "s-fresh",
    "user_id": "alice",
    "max_tokens": 256
  }' | jq -r '.context'
```

The context must mention **Notion**, not Stripe-as-current.

## Step 9 — Test multi-hop recall (Biscuit → Berlin)

The probe doesn't mention Berlin or location, only the dog's name. The service must follow the graph: `Biscuit` → `(user has_pet Biscuit)` → user's other facts → city.

```bash
curl -X POST http://localhost:8080/recall \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "What city does Biscuit live in?",
    "session_id": "s-multihop",
    "user_id": "alice",
    "max_tokens": 256
  }' | jq -r '.context'
```

The context should mention **Berlin**.

## Step 10 — Test noise resistance (topic never discussed)

```bash
curl -X POST http://localhost:8080/recall \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "What kind of car does this user drive?",
    "session_id": "s-noise",
    "user_id": "alice",
    "max_tokens": 256
  }' | jq
```

Expected:

```json
{
  "context": "",
  "citations": []
}
```

The service does **not** hallucinate context. Empty when nothing matches.

## Step 11 — Test the search endpoint (`POST /search`)

`/search` is for agent tool calls — different shape (structured results, not prose):

```bash
curl -X POST http://localhost:8080/search \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "Biscuit",
    "user_id": "alice",
    "limit": 5
  }' | jq
```

Expected: a `results` array with `content`, `score`, `session_id`, `timestamp`, `metadata` for each hit.

## Step 12 — Verify persistence (`docker compose down && up`)

Stop the container without removing the volume:

```bash
docker compose down
```

Bring it back up:

```bash
docker compose up -d
until curl -sf http://localhost:8080/health; do sleep 1; done
```

Confirm Alice's memories are still there:

```bash
curl -s http://localhost:8080/users/alice/memories | jq '.memories | length'
```

Should print a non-zero number — the memories survived the restart because they live in a named Docker volume (`memory_service_data`).

Run a recall to confirm the queryable state survived too:

```bash
curl -X POST http://localhost:8080/recall \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "Where does this user work?",
    "session_id": "s-post-restart",
    "user_id": "alice",
    "max_tokens": 256
  }' | jq -r '.context'
```

Should still mention Notion.

## Step 13 — Test cleanup endpoints (`DELETE`)

Delete a single session:

```bash
curl -X DELETE http://localhost:8080/sessions/s1 -i
```

Expected: `204 No Content`.

Delete everything for a user:

```bash
curl -X DELETE http://localhost:8080/users/alice -i
```

Expected: `204 No Content`.

Confirm:

```bash
curl -s http://localhost:8080/users/alice/memories | jq
# → {"memories": []}
```

## Step 14 — Run the full self-eval harness

Bring the service down (the harness uses an in-process app, not the container):

```bash
docker compose down
```

Then in a Python venv:

```bash
# One-time setup:
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Run the harness:
python scripts/run_eval.py
```

This ingests all 5 fixture scenarios, runs every probe, and prints a coverage table. With a valid OpenAI key it scores 8/8 (100%).

## Step 15 — Run the unit + integration test suite

```bash
source .venv/bin/activate
pytest tests/integration tests/unit -v
```

Should show **23 passed**. These tests don't use OpenAI — they exercise the contract, persistence, concurrency, malformed input, reconciliation, and pure-Python pieces.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `/health` returns 503 or hangs | Container isn't up yet. Run `docker compose logs memory-service` to see startup output. |
| `embedding_available: false` and `llm_available: false` | `OPENAI_API_KEY` not loaded. Check `.env` exists, has no extra quotes, and `docker compose up -d` was re-run after editing it. |
| `/turns` returns 500 with `extraction_failed` in logs | Bad API key, exhausted credit, or rate limit. Check OpenAI dashboard. The turn is still saved — just without extracted memories. |
| `/recall` returns empty for a query you know matches | Either (a) ingestion is still extracting (it's synchronous and takes 5-10s) — wait for the `POST /turns` response, then probe. (b) The probe is genuinely off-topic; rephrase to include a noun the LLM extracted. |
| Persistence test fails | Volume was removed. `docker compose down -v` (note the `-v`) wipes the volume. Use plain `docker compose down` to keep it. |
| Port 8080 already in use | Another process bound to 8080. Either stop it or change the host-side port in `docker-compose.yml` (`"8081:8080"` → service is now at 8081). |
| `docker compose build` fails on `pip install` | Bad network. Retry. If persistent, check that `pyproject.toml` versions still exist on PyPI. |

## How the grader will exercise this

Per the spec §8:

```bash
git clone <your repo> memory-service
cd memory-service
docker compose up -d
until curl -sf http://localhost:8080/health; do sleep 1; done
# their eval harness now points at http://localhost:8080
```

That's the same flow you just walked through. If steps 1–4 work on a fresh checkout with their key, your submission boots.
