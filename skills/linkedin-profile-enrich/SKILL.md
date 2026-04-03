---
name: linkedin-profile-enrich
description: Enrich a CSV of people with LinkedIn profiles using Vertex AI Search + Gemini Flash ranking. Input is name + optional hint (company or title). Two-pass search with parallel execution and checkpoint/resume. Use when asked to find LinkedIn profiles for a list of people.
license: MIT
allowed-tools: Read, Bash(python3:*)
metadata:
  author: syftdata
  version: "1.0"
  user-invocable: "true"
---

# LinkedIn Profile Enrichment

Finds LinkedIn profile URLs for a list of people in a CSV file using Vertex AI Search + Gemini Flash ranking.

## What it does

Given a CSV with names (and optionally a hint column like company name or job title), this skill:
1. Searches Vertex AI Search (indexed on linkedin.com) with a two-pass strategy
2. Filters results to `linkedin.com/in/` URLs only
3. Uses Gemini Flash to rank candidates and pick the best match
4. Outputs an enriched CSV preserving all original columns plus LinkedIn data

## Prerequisites

- GCP Application Default Credentials configured (`gcloud auth application-default login`)
- Vertex AI Search engine indexed on linkedin.com (engine: `syft-li_1772176028063`, project: `640418593024`)
- Python packages: `google-auth`, `google-genai`, `requests`

## CLI Usage

```bash
python3 linkedin_profile_enrich.py \
  --input people.csv \
  --output people_enriched.csv \
  --name-column Name \
  --name-format "last,first" \
  --hint-column Company \
  --linkedin-column Linkedin \
  --context "Speaker at an HR technology conference" \
  --pass2-keywords "HR,talent,people ops" \
  --target-titles "CHRO,VP People,Head of HR" \
  --skip-value "Anonymous user" \
  --test 10 \
  --workers 5
```

### Flags

| Flag | Default | Description |
|---|---|---|
| `--input` | required | Input CSV path |
| `--output` | `{input_stem}_enriched.csv` | Output CSV path |
| `--name-column` | `Name` | Column with person names |
| `--name-format` | `first last` | `last,first` or `first last` |
| `--hint-column` | `""` | Column with hint info (company name or job title) |
| `--linkedin-column` | `""` | Existing LinkedIn column — skip rows that already have a URL |
| `--context` | `""` | Context sentence for Gemini prompt (e.g. "attending a mobility conference") |
| `--pass2-keywords` | `""` | Comma-separated keywords for Pass 2 fallback query |
| `--target-titles` | `""` | Comma-separated title keywords for Title_Match column |
| `--skip-value` | `""` | Row value to skip (e.g. "Anonymous user") |
| `--test` | `0` | Process only first N names |
| `--workers` | `5` | Parallel threads |
| `--checkpoint` | `50` | Save every N rows |

### Output columns added

- `LinkedIn_URL` — Best-match LinkedIn profile URL
- `LinkedIn_Snippet` — Search result snippet
- `LinkedIn_Title` — Title from LinkedIn profile
- `LinkedIn_Company` — Company from LinkedIn profile
- `Confidence` — High, Medium, Low, or None
- `Confidence_Reason` — Why the confidence level was assigned
- `Title_Match` — (only if `--target-titles` provided) Whether the LinkedIn title matches target titles

## How it works

1. **Pass 1**: Search `{name} {hint}` (or just `{name}` if no hint)
2. **Pass 2** (fallback if Pass 1 finds no LinkedIn /in/ URLs): Search `{name} {pass2_keywords}` or `{name} {hint}` with different phrasing
3. Filter to `linkedin.com/in/` results only
4. Gemini Flash ranks results considering name match, hint match, and context
5. Output preserves all original CSV columns + enrichment columns

## Resume logic

On re-run, rows with a LinkedIn URL or Confidence value in the output file are skipped. Rows that failed due to API errors (empty Confidence) are retried automatically.

## Strategy guidance

When the user provides a CSV:
1. **Read the CSV first** to identify the name column and any useful hint columns (company, title, organization)
2. If there's an existing LinkedIn column with some URLs already filled in, use `--linkedin-column` to skip those rows
3. If names are in "Last, First" format, use `--name-format "last,first"`
4. Provide `--context` describing who these people are (e.g. conference attendees, industry professionals)
5. Use `--pass2-keywords` with industry-relevant terms for better fallback results
6. Start with `--test 5` to validate the approach before running the full list

### Example: Name-only CSV (BAMM attendees)

```bash
python3 linkedin_profile_enrich.py \
  --input bamm_attendees.csv \
  --output bamm_enriched.csv \
  --name-format "last,first" \
  --context "Attending BAMM, a global mobility/HR conference" \
  --pass2-keywords "global mobility,immigration,HR,relocation" \
  --skip-value "Anonymous user" \
  --test 5
```

### Example: Name + company hint (Transform speakers)

```bash
python3 linkedin_profile_enrich.py \
  --input speakers.csv \
  --output speakers_enriched.csv \
  --hint-column Company \
  --linkedin-column Linkedin \
  --context "Speaker at Transform Vegas 2026, an HR/people/talent conference" \
  --test 5
```
