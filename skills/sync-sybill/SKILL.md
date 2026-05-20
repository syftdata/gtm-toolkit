---
name: sync-sybill
description: Sync new Sybill call transcripts to the local store at ~/sybill-transcripts/. Use with /loop 15m for continuous polling, or run once to pull the latest calls.
license: MIT
allowed-tools: Bash(python:*)
metadata:
  author: syftdata
  version: "1.0"
---

# sync-sybill

Pulls new Sybill call transcripts into `~/sybill-transcripts/` and reports what was synced.

**Requires:** `~/sybill-transcripts/sync.py` and a valid JWT token at `~/.sybill_token`

## Usage

```
/sync-sybill
```

Or on a recurring schedule:
```
/loop 15m /sync-sybill
```

## Instructions

Run the sync script in incremental mode and report results:

```bash
cd ~/sybill-transcripts && python sync.py
```

After it completes, summarize the output for the user:
- If no new calls: "No new calls since last sync."
- If new calls: "Synced N new call(s):" followed by a brief list with account/topic for each.

If the script prompts for a token (expired or missing), relay the instructions to the user and wait for them to paste the token before re-running.

If the script errors, show the error and suggest running `python sync.py --all` for a fresh full sync.
