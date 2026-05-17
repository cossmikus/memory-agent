"""System prompt + function schema + few-shot examples for the extraction LLM.

Tuning targets:
- Implicit facts: "walking Biscuit this morning" → has_pet:dog named Biscuit
- Corrections: "actually, I meant Notion" → supersedes prior employer
- Salience: filter chitchat; small-talk and politeness markers must score low
- Canonical keys: collapse the long tail to a stable vocabulary so
  reconciliation can match on key equality.
"""
from __future__ import annotations

EXTRACTION_SYSTEM_PROMPT = """\
You extract durable, structured memories about a user from a conversation turn.
You output a JSON list via the `record_memories` tool.

Rules:

1. ONLY extract things that would be useful to remember about the user across
   future conversations. Skip pleasantries ("hi", "thanks"), agent meta-talk,
   and anything ephemeral.

2. Memory `type` values:
   - "fact"       — durable factual statement (employer, location, family)
   - "preference" — what the user likes/dislikes/wants
   - "opinion"    — stance on a topic that may evolve over time
   - "event"      — a specific occurrence (e.g., "moved to Berlin last week")
   - "correction" — explicit correction of an earlier statement

3. Use a CANONICAL KEY when one applies:
   - employer, job_title, location_city, location_country, previous_location_city,
     previous_employer, dietary_restriction, allergy, communication_preference,
     family_member, programming_language_preference, hobby
   - For pets, use "pet:<NAME>:species" and "pet:<NAME>:name".
   - For everything else, use a short, lowercase, snake_case key.

4. Extract IMPLICIT facts too. "walking Biscuit this morning" implies the
   user has a pet named Biscuit. Set "is_implicit": true.

5. For every fact, emit (subject, predicate, object) TRIPLES that capture
   the relationship. Subjects are usually "user". Examples:
   - "I work at Stripe" → triple (user, employer, Stripe)
   - "I have a dog named Biscuit" → triples
       (user, has_pet, Biscuit) and (Biscuit, species, dog)
   - "I just moved to Berlin from NYC" → triples
       (user, location_city, Berlin) and (user, previous_location_city, NYC)
   These power multi-hop retrieval; they matter.

6. confidence ∈ [0,1]: how sure are you the fact is true?
   salience   ∈ [0,1]: how durable / agent-relevant is this for future turns?
   Small-talk should have salience ≤ 0.3 and will be filtered out.

7. evidence_snippet: short verbatim quote from the user that supports the fact.

8. Do NOT extract anything the assistant said unless the user explicitly
   confirmed it. Only the user's statements yield memories.

9. CRITICAL: When an event implies a CHANGE in a durable fact, emit BOTH
   the `event` AND a `fact` with the canonical key. Examples:
   - "I just started at Notion"      → event(employment_start) + fact(employer=Notion)
   - "I moved to Berlin last week"   → event(relocation) + fact(location_city=Berlin)
   - "We got engaged yesterday"      → event(engagement) + fact(relationship_status=engaged)
   Without the parallel fact, supersession of the prior employer/location/etc. cannot fire.
"""

EXTRACTION_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "record_memories",
        "description": "Record extracted structured memories from the conversation.",
        "parameters": {
            "type": "object",
            "properties": {
                "memories": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["fact", "preference", "opinion", "event", "correction"],
                            },
                            "key": {"type": "string"},
                            "value": {"type": "string"},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "salience": {"type": "number", "minimum": 0, "maximum": 1},
                            "is_implicit": {"type": "boolean"},
                            "evidence_snippet": {"type": "string"},
                            "triples": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "subject": {"type": "string"},
                                        "predicate": {"type": "string"},
                                        "object": {"type": "string"},
                                    },
                                    "required": ["subject", "predicate", "object"],
                                },
                            },
                        },
                        "required": ["type", "key", "value", "confidence", "salience"],
                    },
                },
            },
            "required": ["memories"],
        },
    },
}


FEW_SHOTS = [
    {
        "user_messages": [
            "Hey! I work at Stripe as a backend engineer, "
            "and I have a dog named Biscuit who I'm walking right now."
        ],
        "memories": [
            {
                "type": "fact",
                "key": "employer",
                "value": "Stripe",
                "confidence": 0.95,
                "salience": 0.9,
                "is_implicit": False,
                "evidence_snippet": "I work at Stripe",
                "triples": [{"subject": "user", "predicate": "employer", "object": "Stripe"}],
            },
            {
                "type": "fact",
                "key": "job_title",
                "value": "backend engineer",
                "confidence": 0.9,
                "salience": 0.8,
                "is_implicit": False,
                "evidence_snippet": "as a backend engineer",
                "triples": [
                    {"subject": "user", "predicate": "job_title", "object": "backend engineer"}
                ],
            },
            {
                "type": "fact",
                "key": "pet:Biscuit:name",
                "value": "Biscuit",
                "confidence": 0.95,
                "salience": 0.75,
                "is_implicit": True,
                "evidence_snippet": "walking Biscuit this morning",
                "triples": [{"subject": "user", "predicate": "has_pet", "object": "Biscuit"}],
            },
            {
                "type": "fact",
                "key": "pet:Biscuit:species",
                "value": "dog",
                "confidence": 0.95,
                "salience": 0.7,
                "is_implicit": False,
                "evidence_snippet": "a dog named Biscuit",
                "triples": [{"subject": "Biscuit", "predicate": "species", "object": "dog"}],
            },
        ],
    },
    {
        "user_messages": [
            "Sorry I've been quiet. I just moved to Berlin from NYC last week. "
            "Loving the schnitzel."
        ],
        "memories": [
            {
                "type": "fact",
                "key": "location_city",
                "value": "Berlin",
                "confidence": 0.95,
                "salience": 0.95,
                "is_implicit": False,
                "evidence_snippet": "I just moved to Berlin",
                "triples": [{"subject": "user", "predicate": "location_city", "object": "Berlin"}],
            },
            {
                "type": "fact",
                "key": "previous_location_city",
                "value": "NYC",
                "confidence": 0.9,
                "salience": 0.6,
                "is_implicit": True,
                "evidence_snippet": "from NYC last week",
                "triples": [
                    {"subject": "user", "predicate": "previous_location_city", "object": "NYC"}
                ],
            },
            {
                "type": "event",
                "key": "relocation",
                "value": "moved Berlin from NYC",
                "confidence": 0.9,
                "salience": 0.8,
                "is_implicit": False,
                "evidence_snippet": "I just moved to Berlin from NYC last week",
                "triples": [],
            },
        ],
    },
    {
        "user_messages": ["I love TypeScript but Python is what I reach for on weekends."],
        "memories": [
            {
                "type": "preference",
                "key": "programming_language_preference",
                "value": "TypeScript (primary), Python (personal projects)",
                "confidence": 0.9,
                "salience": 0.7,
                "is_implicit": False,
                "evidence_snippet": "I love TypeScript but Python is what I reach for on weekends",
                "triples": [
                    {"subject": "user", "predicate": "loves", "object": "TypeScript"},
                    {"subject": "user", "predicate": "uses", "object": "Python"},
                ],
            },
        ],
    },
    {
        "user_messages": ["Actually, I meant Notion — I just started there last week, not Stripe."],
        "memories": [
            {
                "type": "correction",
                "key": "employer",
                "value": "Notion",
                "confidence": 0.95,
                "salience": 0.95,
                "is_implicit": False,
                "evidence_snippet": "Actually, I meant Notion — I just started there last week",
                "triples": [{"subject": "user", "predicate": "employer", "object": "Notion"}],
            },
        ],
    },
]
