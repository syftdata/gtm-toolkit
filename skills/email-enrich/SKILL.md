---
name: email-enrich
description: Enrich a CSV of personal email addresses (e.g. Gmail) into professional LinkedIn profiles using Vertex AI Search (general web + LinkedIn engines) + Gemini Flash. Uses email handle as a cross-platform identifier. Use when asked to find LinkedIn profiles from email addresses.
license: MIT
allowed-tools: Read, Bash(python3:*)
metadata:
  author: syftdata
  version: "1.0"
  user-invocable: "true"
---

# Email Address Enrichment

Finds LinkedIn profiles for people given only their personal email addresses (Gmail, Yahoo, etc.) using Vertex AI Search + Gemini Flash.

## What it does

Given a CSV with email addresses (and optionally names), this skill:
1. Searches a general web engine (indexed on ~100 social/professional sites) for the email and handle
2. Uses Gemini Flash to extract signals: real name, LinkedIn URL, handle reuse, company, title
3. Validates and resolves LinkedIn profiles via a dedicated LinkedIn search engine
4. Outputs an enriched CSV preserving all original columns plus LinkedIn data

## Prerequisites

- GCP Application Default Credentials configured (`gcloud auth application-default login`)
- Two Vertex AI Search engines:
  - General web engine indexed on ~100 social/professional domains (engine: `syft-general_1776126821000`, project: `640418593024`)
  - LinkedIn engine indexed on linkedin.com (engine: `syft-li_1772176028063`, project: `640418593024`)
- Python packages: `google-auth`, `google-genai`, `requests`

## CLI Usage

```bash
python3 email_enrich.py \
  --input people.csv \
  --output people_enriched.csv \
  --email-column Email \
  --name-column Name \
  --context "Personal Gmail addresses of conference attendees" \
  --test 5 \
  --workers 5
```

### Flags

| Flag | Default | Description |
|---|---|---|
| `--input` | required | Input CSV path |
| `--output` | `{input_stem}_enriched.csv` | Output CSV path |
| `--email-column` | `Email` | Column with email addresses |
| `--name-column` | `""` | Column with person names (optional, improves matching) |
| `--context` | `""` | Context sentence for Gemini prompt (e.g. "conference attendees") |
| `--skip-value` | `""` | Row value to skip (e.g. "Anonymous user") |
| `--test` | `0` | Process only first N rows |
| `--workers` | `5` | Parallel threads |
| `--checkpoint` | `50` | Save every N rows |

### Output columns added

- `LinkedIn_URL` - Best-match LinkedIn profile URL
- `LinkedIn_Snippet` - Search result snippet
- `LinkedIn_Title` - Title from LinkedIn profile
- `LinkedIn_Company` - Company from LinkedIn profile
- `Confidence` - High, Medium, Low, or None
- `Confidence_Reason` - Why the confidence level was assigned
- `Web_Evidence` - Summary of web search signals found

## How it works

### Algorithm

1. **Web Search** (progressive, quoted queries on general web engine):
   a. `"email@gmail.com"` (exact email match)
   b. `"handle" firstname lastname` (quoted handle + unquoted name, skips fake/placeholder names)
   c. Stop at first query that returns results, then Gemini Flash extracts signals: real name, LinkedIn URL, handle, company, title

2. **Find LinkedIn** (resolve and validate a LinkedIn profile):
   a. If web search found a LinkedIn URL, validate it exists in the LinkedIn index and name matches
   b. If web search found a reused handle, try `linkedin.com/in/{handle}` and validate
   c. Otherwise, search the LinkedIn engine:
      - `"{handle}"` (quoted exact match)
      - `"{csv_name}"` (unquoted name search)
      - Gemini ranks candidates using all accumulated context
      - Rejects name-only matches without corroborating evidence (handle, company, location)

3. **Confidence Assignment**: High (strong multi-signal match), Medium (partial corroboration), Low (weak match), None (no match found)

### Key design decisions

- **Email handle as identifier**: People commonly reuse their email handle (e.g. `jsmith42`) across GitHub, LinkedIn, and other platforms, making it a strong cross-platform signal
- **Quoted search for precision**: Quoted searches prevent keyword tokenization (e.g. `"svetlana.kremen"` instead of `svetlana kremen gmail com`)
- **Strict rejection of name-only matches**: Gemini is instructed to reject LinkedIn matches based solely on name without corroborating evidence, reducing false positives
- **Fake name filtering**: Skips placeholder names like "Unknown", "Test", or single-character names

## Resume logic

On re-run, rows with a LinkedIn URL or Confidence value in the output file are skipped. Rows that failed due to API errors (empty Confidence) are retried automatically.

## Strategy guidance

When the user provides a CSV of email addresses:
1. **Read the CSV first** to identify the email column and any name columns
2. If names are in separate First/Last columns, combine them or pick the most useful one for `--name-column`
3. Provide `--context` describing who these people are (e.g. "attendees at a tech conference")
4. Start with `--test 5` to validate the approach before running the full list
5. This skill works best with personal email addresses (Gmail, Yahoo, Hotmail). For work emails, consider using the `linkedin-profile-enrich` skill with name + company instead.

### Example: Gmail addresses with names

```bash
python3 email_enrich.py \
  --input contacts.csv \
  --output contacts_enriched.csv \
  --email-column Email \
  --name-column "Full Name" \
  --context "Personal Gmail addresses from a CRM export" \
  --test 5
```

### Example: Email-only CSV (no names)

```bash
python3 email_enrich.py \
  --input emails.csv \
  --output emails_enriched.csv \
  --email-column email_address \
  --context "Gmail addresses collected from event registrations" \
  --workers 3
```
