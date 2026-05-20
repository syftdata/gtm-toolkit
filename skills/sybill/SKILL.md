---
name: sybill
description: Query your local Sybill transcript store. Ask about specific accounts, deals, action items, or search across calls. Uses ~/sybill-transcripts/ as the source of truth.
license: MIT
allowed-tools: Bash(sqlite3:*, python:*), Read
metadata:
  author: syftdata
  version: "1.0"
---

# sybill

Answer questions about sales calls using the local transcript store at `~/sybill-transcripts/`.

**Requires:** `~/sybill-transcripts/transcripts.db` and `~/sybill-transcripts/calls/{callId}.md` files written by `sync.py`.

## Usage

```
/sybill <natural language question>
```

### Examples

```
/sybill what's going on with Bloomberg Beta?
/sybill action items from this week
/sybill how did the Tonic demo go?
/sybill calls with Rachel Kirkwood
/sybill deals owned by imran@syftdata.com
```

## Instructions

### Step 1: Find relevant calls via SQLite

Use `sqlite3` or Python to query `~/sybill-transcripts/transcripts.db`.

Common query patterns:

**By account name or domain:**
```sql
SELECT call_id, topic, start_time, account_name, account_domain
FROM calls
WHERE account_name LIKE '%Bloomberg%' OR account_domain LIKE '%bloomberg%'
ORDER BY start_time DESC;
```

**By participant:**
```sql
SELECT c.call_id, c.topic, c.start_time, c.account_name
FROM calls c
JOIN participants p ON c.call_id = p.call_id
WHERE p.name LIKE '%Rachel%' OR p.email LIKE '%rachel%'
ORDER BY c.start_time DESC;
```

**By date range (this week):**
```sql
SELECT call_id, topic, start_time, account_name
FROM calls
WHERE start_time >= (strftime('%s', 'now', '-7 days') * 1000)
ORDER BY start_time DESC;
```

**By deal owner:**
```sql
SELECT call_id, topic, start_time, account_name
FROM calls
WHERE deal_owner_email = 'imran@syftdata.com'
ORDER BY start_time DESC;
```

**By meeting type:**
```sql
SELECT call_id, topic, start_time, account_name
FROM calls
WHERE meeting_type = 'EXTERNAL'
ORDER BY start_time DESC
LIMIT 10;
```

### Step 2: Read relevant markdown files

For each matching `call_id`, read the full transcript:
```
~/sybill-transcripts/calls/{call_id}.md
```

The markdown contains:
- Header with date, duration, account, type, participants
- `## Summary` with Outcome, Pain Points, Next Steps sections
- `## Transcript` with timestamped speaker lines

### Step 3: Answer the question

Based on the user's question, synthesize the relevant information:

**"What's going on with X?"** - Read all calls for that account, summarize deal status, recent discussion, open questions.

**"Action items from this week"** - Find calls in the last 7 days, extract Next Steps sections and any tasks mentioned in transcripts.

**"How did the [meeting] go?"** - Read that specific call, summarize outcome, key moments, sentiment.

**"Calls with [person]"** - List calls they appeared in, brief summary of each.

### Format guidelines

- Lead with a direct answer to the question
- For account summaries: group by call, newest first, with date and key points
- For action items: use a checklist format grouped by account/deal
- For specific call summaries: include outcome, key takeaways, and next steps
- Reference the call date and participants for context
- If no calls are found, say so and suggest running `/sync-sybill` to pull latest data

### Error handling

If `transcripts.db` does not exist or has no rows:
```
No transcript data found. Run /sync-sybill (or `python ~/sybill-transcripts/sync.py --all` for a full backfill) to populate the store.
```
