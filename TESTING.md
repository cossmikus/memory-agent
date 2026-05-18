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

# Advanced Scenarios

These exercise paths and behaviors not covered by Steps 5–13. Each scenario is independent (own `user_id`), so you can run any one in any order. Each follows the same pattern: setup → verify → cleanup.

> **Quoting tip.** A JSON query like `"What is this user's favorite movie?"` contains an apostrophe, which closes a single-quoted bash string and drops you into `dquote>` continuation mode. Either rephrase to avoid `'` or use a heredoc (`--data @- <<'JSON'`). The examples below avoid apostrophes for paste-safety.

## A — Multi-user isolation (no bleed)

Two users with similar topics. Each user's recall must see only their own facts.

```bash
curl -X POST http://localhost:8080/turns \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "bob-s1", "user_id": "bob",
    "messages": [{"role": "user", "content": "I work at Datadog as a site reliability engineer. I live in San Francisco."}],
    "timestamp": "2025-04-01T09:00:00Z", "metadata": {}
  }' | jq

curl -X POST http://localhost:8080/turns \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "carol-s1", "user_id": "carol",
    "messages": [{"role": "user", "content": "I work at Anthropic. I live in London."}],
    "timestamp": "2025-04-01T10:00:00Z", "metadata": {}
  }' | jq

# Bob's recall — must mention Datadog/SF, NOT Anthropic/London
curl -X POST http://localhost:8080/recall \
  -H 'Content-Type: application/json' \
  -d '{"query":"Where does this user work and live?","session_id":"bob-r","user_id":"bob","max_tokens":256}' | jq -r '.context'

# Carol's recall — must mention Anthropic/London, NOT Datadog/SF
curl -X POST http://localhost:8080/recall \
  -H 'Content-Type: application/json' \
  -d '{"query":"Where does this user work and live?","session_id":"carol-r","user_id":"carol","max_tokens":256}' | jq -r '.context'
```

**Expect:** Bob's context = Datadog + San Francisco only. Carol's context = Anthropic + London only. Zero cross-contamination.

## B — Implicit fact extraction

The user never says "I have a dog" — but mentions walking Biscuit. The LLM should infer the pet exists.

```bash
curl -X POST http://localhost:8080/turns \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "dan-s1", "user_id": "dan",
    "messages": [{"role": "user", "content": "Sorry, had to step away. Was walking Biscuit through the park."}],
    "timestamp": "2025-04-03T09:00:00Z", "metadata": {}
  }' | jq

# Did the LLM catch the implicit ownership?
curl -s http://localhost:8080/users/dan/memories \
  | jq '[.memories[] | select(.key | startswith("pet"))]'

# And recall it
curl -X POST http://localhost:8080/recall \
  -H 'Content-Type: application/json' \
  -d '{"query":"Does this user have any pets?","session_id":"dan-r","user_id":"dan","max_tokens":256}' | jq -r '.context'
```

**Expect:** `/users/dan/memories` includes a `pet:Biscuit:*` or `pet:biscuit:*` row. The recall context mentions Biscuit even though the user never stated direct ownership.

## C — Multi-valued attributes (allergies, dietary, dislikes)

Multiple values for the same attribute family must coexist. Both allergies stay active — neither supersedes the other.

```bash
curl -X POST http://localhost:8080/turns \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "eve-s1", "user_id": "eve",
    "messages": [{"role": "user", "content": "Before we order food: I am vegetarian, allergic to shellfish and peanuts, and I really dislike cilantro."}],
    "timestamp": "2025-04-04T09:00:00Z", "metadata": {}
  }' | jq

# Both allergies must be active (no supersession between them)
curl -s http://localhost:8080/users/eve/memories \
  | jq '.memories | map(select(.key | startswith("allergy"))) | map({key, value, active})'

# Recall on a food-safety question
curl -X POST http://localhost:8080/recall \
  -H 'Content-Type: application/json' \
  -d '{"query":"What should I know before ordering food for this user?","session_id":"eve-r","user_id":"eve","max_tokens":512}' | jq -r '.context'
```

**Expect:** `allergy:shellfish` and `allergy:peanuts` BOTH `active: true`. Recall context mentions vegetarian, shellfish, peanuts, cilantro.

## D — Fact evolution (Stripe → Notion supersession chain)

Two sessions, different employer values. The chain must be preserved.

```bash
curl -X POST http://localhost:8080/turns \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "alice-s1", "user_id": "alice-evo",
    "messages": [{"role": "user", "content": "I work at Stripe as a backend engineer."}],
    "timestamp": "2025-03-10T09:00:00Z", "metadata": {}
  }' | jq

curl -X POST http://localhost:8080/turns \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "alice-s2", "user_id": "alice-evo",
    "messages": [{"role": "user", "content": "Big update — I just started at Notion."}],
    "timestamp": "2025-05-01T11:00:00Z", "metadata": {}
  }' | jq

# Both employer rows visible; Stripe inactive, Notion active, supersedes set
curl -s http://localhost:8080/users/alice-evo/memories \
  | jq '[.memories[] | select(.key == "employer")] | map({value, active, supersedes})'

# Recall must surface the CURRENT employer
curl -X POST http://localhost:8080/recall \
  -H 'Content-Type: application/json' \
  -d '{"query":"Where does this user currently work?","session_id":"alice-r","user_id":"alice-evo","max_tokens":256}' | jq -r '.context'
```

**Expect:** Two `employer` rows. Stripe `active: false`; Notion `active: true, supersedes: <stripe-id>`. Recall mentions Notion, not Stripe-as-current.

## E — Reinforcement (same fact restated → no duplicate row)

The same fact stated twice in different sessions should bump confidence, not create a second row.

```bash
curl -X POST http://localhost:8080/turns \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "gina-s1", "user_id": "gina-rein",
    "messages": [{"role": "user", "content": "I work at Linear."}],
    "timestamp": "2025-03-01T09:00:00Z", "metadata": {}
  }' | jq

curl -X POST http://localhost:8080/turns \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "gina-s2", "user_id": "gina-rein",
    "messages": [{"role": "user", "content": "Yep, still at Linear, btw."}],
    "timestamp": "2025-03-15T09:00:00Z", "metadata": {}
  }' | jq

curl -s http://localhost:8080/users/gina-rein/memories \
  | jq '[.memories[] | select(.key == "employer")] | map({value, active, confidence, history})'
```

**Expect:** exactly one `employer = Linear` row, `active: true`, `confidence` bumped above the initial 0.95 (typically to 0.97), and `history` array contains a `kind: "reinforce"` entry.

## F — Opinion evolution arc

Three sessions, shifting stance. Recall must return the *current* nuanced position.

```bash
curl -X POST http://localhost:8080/turns \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "frank-s1", "user_id": "frank-arc",
    "messages": [{"role": "user", "content": "I love TypeScript. It is perfect for everything."}],
    "timestamp": "2025-01-10T09:00:00Z", "metadata": {}
  }' | jq

curl -X POST http://localhost:8080/turns \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "frank-s2", "user_id": "frank-arc",
    "messages": [{"role": "user", "content": "TypeScript generics are getting annoying lately."}],
    "timestamp": "2025-03-15T09:00:00Z", "metadata": {}
  }' | jq

curl -X POST http://localhost:8080/turns \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "frank-s3", "user_id": "frank-arc",
    "messages": [{"role": "user", "content": "TypeScript is fine for big projects but I prefer Python for scripts now."}],
    "timestamp": "2025-05-01T09:00:00Z", "metadata": {}
  }' | jq

# Inspect the arc
curl -s http://localhost:8080/users/frank-arc/memories \
  | jq '.memories | map({type, key, value, active, history})'

# Recall current stance
curl -X POST http://localhost:8080/recall \
  -H 'Content-Type: application/json' \
  -d '{"query":"How does this user feel about TypeScript right now?","session_id":"frank-r","user_id":"frank-arc","max_tokens":256}' | jq -r '.context'
```

**Expect:** Recall returns the **current** nuanced position ("Python for scripts, TypeScript for big projects" or similar) — not "perfect for everything". Historical stances visible in memories (either via supersession chain or via a `history` array, depending on which keys the LLM picked).

## G — Multi-message turn with tool roles

Realistic agent shape: user → assistant → tool → assistant → user. Extraction must only fire on user messages.

```bash
curl -X POST http://localhost:8080/turns \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "tool-s1", "user_id": "tool-user",
    "messages": [
      {"role": "user", "content": "Book me a flight to Tokyo for next month."},
      {"role": "assistant", "content": "Looking up flights..."},
      {"role": "tool", "name": "flight_search", "content": "{\"flights\":[{\"airline\":\"ANA\",\"price\":720}]}"},
      {"role": "assistant", "content": "I found ANA at $720. Book it?"},
      {"role": "user", "content": "Yes — my budget is $800 max so that works."}
    ],
    "timestamp": "2025-05-01T09:00:00Z", "metadata": {}
  }' | jq

curl -s http://localhost:8080/users/tool-user/memories | jq '.memories | map({type, key, value})'
```

**Expect:** `201 Created`. Memories include something around the user's budget preference ($800) — derived from the user's final message. Crucially **no memory** claims "user is flying ANA" (the tool *offered* it; the user didn't *confirm* the booking). Assistant/tool messages do not pollute the user's memory.

## H — Anonymous turn (`user_id: null`)

Spec allows null. Turn is stored, but no memories are extracted without a user to anchor them to.

```bash
curl -X POST http://localhost:8080/turns \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "anon-s1", "user_id": null,
    "messages": [{"role": "user", "content": "Just testing — no user attached."}],
    "timestamp": "2025-05-01T09:00:00Z", "metadata": {}
  }' -i 2>&1 | head -3

# Anonymous recall is empty by design — no user to anchor memories to
curl -X POST http://localhost:8080/recall \
  -H 'Content-Type: application/json' \
  -d '{"query":"anything","session_id":"anon-r","user_id":null,"max_tokens":256}' | jq
```

**Expect:** `201 Created` on the turn. Recall returns `{"context": "", "citations": []}`.

## I — Tight token budget (assembler respects `max_tokens`)

The assembler must trim its prose to fit a small budget without overshoot.

```bash
curl -X POST http://localhost:8080/turns \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "tight-s1", "user_id": "tight-user",
    "messages": [{"role": "user", "content": "I am a marine biologist who lives in Reykjavik and studies orcas."}],
    "timestamp": "2025-05-01T09:00:00Z", "metadata": {}
  }' > /dev/null

# Standard budget — should mention marine biologist and Reykjavik
curl -X POST http://localhost:8080/recall \
  -H 'Content-Type: application/json' \
  -d '{"query":"profession","session_id":"tight-r1","user_id":"tight-user","max_tokens":512}' | jq -r '.context'

# Tight budget — context must be much shorter but still informative
curl -X POST http://localhost:8080/recall \
  -H 'Content-Type: application/json' \
  -d '{"query":"profession","session_id":"tight-r2","user_id":"tight-user","max_tokens":48}' | jq -r '.context'
```

**Expect:** First call returns a full context. Second call returns a short context (≤ ~50 tokens) but still mentions marine biologist or Reykjavik.

## J — Unicode and emoji extraction

Foreign-script names, emoji, mixed scripts must round-trip cleanly.

```bash
curl -X POST http://localhost:8080/turns \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "uni-s1", "user_id": "uni-user",
    "messages": [{"role": "user", "content": "I live in München 🇩🇪 and my cat is named 雪 (Yuki)."}],
    "timestamp": "2025-05-01T09:00:00Z", "metadata": {}
  }' | jq

curl -s http://localhost:8080/users/uni-user/memories | jq '.memories | map({key, value})'
```

**Expect:** `201 Created`. Memories include `location_city = München` (or `Munich`) and a pet memory with `雪` or `Yuki` somewhere in the value.

## K — Optional Bearer auth

If you set `MEMORY_AUTH_TOKEN` in `.env`, the service requires `Authorization: Bearer <token>` on every route except `/health`.

```bash
# 1) Add MEMORY_AUTH_TOKEN=secret-123 to .env
# 2) Restart: docker compose down && docker compose up -d
# 3) Wait: until curl -sf http://localhost:8080/health; do sleep 1; done

# Without token → 401
curl -X POST http://localhost:8080/turns \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"a","user_id":"a","messages":[{"role":"user","content":"hi"}],"timestamp":"2025-05-01T09:00:00Z","metadata":{}}' \
  -s -o /dev/null -w "%{http_code}\n"

# Wrong token → 401
curl -X POST http://localhost:8080/turns \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer wrong-token' \
  -d '{"session_id":"a","user_id":"a","messages":[{"role":"user","content":"hi"}],"timestamp":"2025-05-01T09:00:00Z","metadata":{}}' \
  -s -o /dev/null -w "%{http_code}\n"

# Correct token → 201
curl -X POST http://localhost:8080/turns \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer secret-123' \
  -d '{"session_id":"a","user_id":"a","messages":[{"role":"user","content":"hi"}],"timestamp":"2025-05-01T09:00:00Z","metadata":{}}' \
  -s -o /dev/null -w "%{http_code}\n"

# /health is always allowed even without a token
curl -s http://localhost:8080/health -o /dev/null -w "%{http_code}\n"
```

**Expect:** `401`, `401`, `201`, `200` — in that order. Remove `MEMORY_AUTH_TOKEN` from `.env` and restart when done if you want to go back to no-auth mode.

## L — Long content stress test

Spec requires the service to survive "oversized payloads" without crashing. We reject payloads >1 MiB at the middleware (`413`); inside that limit, large user messages should ingest cleanly.

```bash
# ~10 KB of repeated content embedded in a turn — well under the 1 MiB cap
BIG_CONTENT=$(python3 -c "print('I work at Stripe. ' * 500)")
curl -X POST http://localhost:8080/turns \
  -H 'Content-Type: application/json' \
  -d "$(jq -n --arg c "$BIG_CONTENT" '{
    session_id: "big-s1", user_id: "big-user",
    messages: [{role: "user", content: $c}],
    timestamp: "2025-05-01T09:00:00Z", metadata: {}
  }')" \
  -s -o /dev/null -w "%{http_code}\n"
```

**Expect:** `201`. The service accepts the long message, ingests it, the extractor sees lots of repetition and extracts a single `employer = Stripe` memory.

## M — Mass cleanup

Run this at the end to remove all the scenario users you created.

```bash
for u in bob carol dan eve alice-evo gina-rein frank-arc tool-user tight-user uni-user big-user; do
  curl -X DELETE http://localhost:8080/users/$u -s -o /dev/null -w "$u: %{http_code}\n"
done
```

**Expect:** every line prints `204`.

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
