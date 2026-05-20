#!/usr/bin/env python3 -u
"""
Enrich a CSV of personal email addresses into professional LinkedIn profiles.

Uses a two-engine strategy:
  1. General web search (Vertex AI) to find the email handle across social/professional sites
  2. LinkedIn search (Vertex AI) to resolve and validate LinkedIn profiles
  3. Gemini Flash to extract signals, rank candidates, and validate matches

Algorithm:
  1. Web search: "email@gmail.com" (exact), then "handle" + name (quoted handle)
     -> Gemini extracts: {real_name, linkedin_url, handle, company, title}
  2. Find LinkedIn:
     a. If Step 1 found a linkedin_url -> validate (must be in LinkedIn index + name match)
     b. If Step 1 found a handle -> try linkedin.com/in/{handle} -> validate
     c. Else -> LinkedIn search: "{handle}" (quoted), then "{csv_name}" (unquoted)
        -> Gemini ranks using all context, rejects name-only matches without corroboration
  3. Confidence: High/Medium/Low/None

Usage:
    python3 email_enrich.py \
      --input people.csv \
      --output people_enriched.csv \
      --email-column Email \
      --name-column Name \
      --context "Personal Gmail addresses" \
      --test 5
"""

import csv
import json
import os
import re
import sys
import time
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
from typing import Optional

import google.auth
import google.auth.transport.requests
import requests as http_requests
from google import genai

sys.path.insert(0, str(Path(__file__).resolve().parent))
from json_extraction import extract_and_parse_json

# ── API config ───────────────────────────────────────────────────────────────
GEMINI_MODEL = "gemini-2.5-flash"
VERTEX_LOCATION = "us-central1"
GOOGLE_CLOUD_PROJECT = "ornate-acronym-372603"

VERTEX_SEARCH_PROJECT = "640418593024"

# General web search engine (indexed on ~100 social/professional sites)
VERTEX_WEB_ENGINE = "syft-general_1776126821000"
VERTEX_WEB_URL = (
    f"https://discoveryengine.googleapis.com/v1alpha/projects/{VERTEX_SEARCH_PROJECT}"
    f"/locations/global/collections/default_collection"
    f"/engines/{VERTEX_WEB_ENGINE}/servingConfigs/default_search:search"
)

# LinkedIn search engine (indexed on linkedin.com)
VERTEX_LI_ENGINE = "syft-li_1772176028063"
VERTEX_LI_URL = (
    f"https://discoveryengine.googleapis.com/v1alpha/projects/{VERTEX_SEARCH_PROJECT}"
    f"/locations/global/collections/default_collection"
    f"/engines/{VERTEX_LI_ENGINE}/servingConfigs/default_search:search"
)

SEARCH_DELAY = 0.3

# ── Thread-safe progress ────────────────────────────────────────────────────
_progress_lock = Lock()
_progress_count = 0
_total_to_process = 0
_found_count = 0

# ── Auth ─────────────────────────────────────────────────────────────────────
_auth_lock = Lock()
_credentials = None
_token_expiry = 0


def _get_access_token() -> str:
    global _credentials, _token_expiry
    with _auth_lock:
        now = time.time()
        if _credentials is None or now >= _token_expiry - 60:
            _credentials, _ = google.auth.default()
            _credentials.refresh(google.auth.transport.requests.Request())
            _token_expiry = now + 3500
        return _credentials.token


def init_gemini() -> genai.Client:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", GOOGLE_CLOUD_PROJECT)
    return genai.Client(vertexai=True, project=project, location=VERTEX_LOCATION)


# ── Vertex AI Search ────────────────────────────────────────────────────────


def vertex_search(query: str, engine_url: str) -> Optional[list]:
    """Call Vertex AI Search. Returns list of items or None on failure."""
    token = _get_access_token()
    body = {
        "query": query,
        "pageSize": 10,
        "queryExpansionSpec": {"condition": "AUTO"},
        "spellCorrectionSpec": {"mode": "AUTO"},
    }

    for attempt in range(3):
        try:
            resp = http_requests.post(
                engine_url,
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                items = []
                for res in data.get("results", []):
                    doc = res.get("document", {})
                    derived = doc.get("derivedStructData", {})
                    link = derived.get("link", "")
                    title = derived.get("title", "")
                    snippets = derived.get("snippets", [])
                    snippet = snippets[0].get("snippet", "") if snippets else ""
                    items.append({"link": link, "title": title, "snippet": snippet})
                return items
            elif resp.status_code == 429:
                wait = (2 ** attempt) * 2
                print(f"  429 rate-limit, sleeping {wait}s", flush=True)
                time.sleep(wait)
            else:
                print(f"  Vertex Search {resp.status_code}: {resp.text[:200]}", flush=True)
                return None
        except http_requests.RequestException as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                print(f"  Search error: {e}", flush=True)
    return None


def filter_linkedin_results(items: list) -> list:
    return [item for item in items if "linkedin.com/in/" in item.get("link", "")]


# ── Helpers ──────────────────────────────────────────────────────────────────


def get_email_handle(email: str) -> str:
    """Extract the local part before @ as-is."""
    return email.split("@")[0] if "@" in email else email


def is_fake_name(name: str) -> bool:
    """Check if CSV name looks fake/placeholder."""
    if not name:
        return True
    lower = name.strip().lower()
    fake_indicators = ["whatever", "test", "unknown", "n/a", "none", "null", "anonymous"]
    if lower in fake_indicators:
        return True
    if len(lower) <= 2:
        return True
    return False


# ── Step 1: Web search for email ─────────────────────────────────────────────


def web_search_email(email: str, csv_name: str = "") -> Optional[list]:
    """Search for the email on the web engine. Try quoted email, then quoted handle + name."""
    # 1a: exact email quoted
    results = vertex_search(f'"{email}"', VERTEX_WEB_URL)
    if results:
        return results
    time.sleep(SEARCH_DELAY)

    # 1b: quoted handle + unquoted name for context (skip junk names)
    handle = get_email_handle(email)
    query = f'"{handle}"'
    if csv_name and not is_fake_name(csv_name):
        query = f'"{handle}" {csv_name}'
    results = vertex_search(query, VERTEX_WEB_URL)
    return results


def extract_web_signals(client: genai.Client, email: str, csv_name: str, web_results: list) -> dict:
    """Use Gemini to extract identity signals from web search results."""
    handle = get_email_handle(email)

    results_for_prompt = []
    for i, item in enumerate(web_results[:10]):
        results_for_prompt.append({
            "position": i + 1,
            "url": item.get("link", ""),
            "title": item.get("title", ""),
            "snippet": item.get("snippet", ""),
        })

    prompt = f"""I'm trying to identify the person who owns the email address: {email}
The email handle (before @) is: {handle}
The name we have on file is: {csv_name}

Here are web search results for this email/handle:
{json.dumps(results_for_prompt, indent=2)}

Analyze these results and extract any identity signals about the email owner.
Look for: their real name, LinkedIn profile URL, GitHub or other usernames/handles,
current company, job title, location.

Return ONLY valid JSON:
{{"real_name": "<full name if found, or empty string>", "linkedin_url": "<linkedin.com/in/... URL if found directly in the results, or empty string>", "handle": "<username/handle if found on GitHub or other platforms, or empty string>", "company": "<current company if found, or empty string>", "title": "<job title if found, or empty string>", "location": "<location if found, or empty string>", "evidence": "<brief summary of what you found and where>"}}

IMPORTANT: Only return a linkedin_url if you see an actual LinkedIn URL in the search results. Do NOT guess or construct LinkedIn URLs.
If the results don't seem related to this email/person, return empty strings for all fields."""

    for attempt in range(3):
        try:
            response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
            text = (response.text or "").strip()
            result = extract_and_parse_json(text)
            if result is not None:
                return result
            if attempt < 2:
                time.sleep(1)
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                print(f"  Gemini signal extraction error: {e}", flush=True)
    return {}


# ── Step 2a: Validate a LinkedIn URL found from web search ──────────────────


def validate_linkedin_url(client: genai.Client, linkedin_url: str, csv_name: str, email: str, handle: str) -> Optional[dict]:
    """Validate a LinkedIn URL by checking it exists in the LinkedIn index and name matches."""
    items = vertex_search(linkedin_url, VERTEX_LI_URL)
    time.sleep(SEARCH_DELAY)

    li_items = filter_linkedin_results(items or [])

    # Find the matching URL in results (normalize by slug)
    target_slug = ""
    m = re.search(r'linkedin\.com/in/([^/?]+)', linkedin_url.lower().rstrip("/"))
    if m:
        target_slug = m.group(1)

    matching = [
        item for item in li_items
        if target_slug and target_slug in item.get("link", "").lower()
    ]

    if not matching:
        # URL not in LinkedIn index, reject it (could be hallucinated)
        return None

    item = matching[0]
    prompt = f"""Validate this LinkedIn profile match.
Target email: {email}
Email handle: {handle}
Name on file: {csv_name}

LinkedIn profile:
URL: {item.get("link", "")}
Title: {item.get("title", "")}
Snippet: {item.get("snippet", "")}

Does this profile plausibly belong to the person with email {email}?
Consider: does the name match? Does the handle match the LinkedIn slug?

Return ONLY valid JSON:
{{"linkedin_url": "{item.get("link", "")}", "linkedin_title": "<title>", "linkedin_company": "<company or empty>", "snippet": "<snippet>", "confidence": "High|Medium|Low", "confidence_reason": "<explanation>"}}

If this is clearly NOT the right person, return {{"linkedin_url": "", "confidence": "None", "confidence_reason": "<why not>"}}"""

    for attempt in range(3):
        try:
            response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
            text = (response.text or "").strip()
            result = extract_and_parse_json(text)
            if result is not None:
                return result
            if attempt < 2:
                time.sleep(1)
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                print(f"  Gemini validation error: {e}", flush=True)
    return None


# ── Step 2c: LinkedIn search + ranking ──────────────────────────────────────


def search_and_rank_linkedin(
    client: genai.Client, csv_name: str, email: str, handle: str,
    web_signals: dict, context: str
) -> Optional[dict]:
    """Search LinkedIn engine and rank results with full context.
    Tries quoted handle first, then csv_name."""
    li_items = []

    # Try handle first (quoted for exact match)
    items = vertex_search(f'"{handle}"', VERTEX_LI_URL)
    time.sleep(SEARCH_DELAY)
    li_items = filter_linkedin_results(items or [])

    # Then try csv_name
    if not li_items and csv_name:
        items2 = vertex_search(csv_name, VERTEX_LI_URL)
        time.sleep(SEARCH_DELAY)
        li_items = filter_linkedin_results(items2 or [])

    if not li_items:
        return None

    # Gemini ranking with full context
    results_for_prompt = []
    for i, item in enumerate(li_items):
        results_for_prompt.append({
            "position": i + 1,
            "url": item.get("link", ""),
            "title": item.get("title", ""),
            "snippet": item.get("snippet", ""),
        })

    web_name = web_signals.get("real_name", "")
    web_company = web_signals.get("company", "")
    web_title = web_signals.get("title", "")
    evidence = web_signals.get("evidence", "")

    extra_context = ""
    if web_name:
        extra_context += f"\nName from web search: {web_name}"
    if web_company:
        extra_context += f"\nCompany from web search: {web_company}"
    if web_title:
        extra_context += f"\nTitle from web search: {web_title}"
    if evidence:
        extra_context += f"\nWeb evidence: {evidence}"
    if context:
        extra_context += f"\nContext: {context}"

    prompt = f"""LinkedIn profile matching.
Email: {email}
Email handle: {handle}
Name on file: {csv_name}{extra_context}

LinkedIn search results:
{json.dumps(results_for_prompt)}

Pick the best match. Consider:
- Does the email handle EXACTLY match the LinkedIn slug? (e.g. handle "benjdod.dev" matches slug "benjdod")
- Does the name match the name on file or the web-discovered name?
- If company/title are known from web search, do they match?

IMPORTANT: If the only signal is a name match with no corroborating evidence (no handle match, no web evidence, no company match), return no match. Common names like "Ryan Smith" or "Kelsey McDonald" will have many LinkedIn profiles. Do NOT guess which one is correct without a strong corroborating signal.

Return ONLY valid JSON:
{{"linkedin_url": "<url>", "linkedin_title": "<title from snippet>", "linkedin_company": "<company or empty>", "snippet": "<snippet verbatim>", "confidence": "High|Medium|Low", "confidence_reason": "<brief explanation>"}}

If none of the results are a plausible match or there is insufficient evidence, return {{"linkedin_url": "", "confidence": "None", "confidence_reason": "No plausible match"}}"""

    for attempt in range(3):
        try:
            response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
            text = (response.text or "").strip()
            result = extract_and_parse_json(text)
            if result is not None:
                return result
            if attempt < 2:
                time.sleep(1)
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                print(f"  Gemini ranking error: {e}", flush=True)
    return None


# ── Main enrichment per row ──────────────────────────────────────────────────


def enrich_one(row: dict, client: genai.Client, email_column: str, name_column: str, context: str) -> dict:
    """Enrich a single email address. Returns updated row dict."""
    global _progress_count, _found_count

    email = row[email_column].strip().lower()
    csv_name = row.get(name_column, "").strip() if name_column else ""
    handle = get_email_handle(email)

    result_row = dict(row)

    # Step 1: Web search (quoted email, then quoted handle + name)
    web_results = web_search_email(email, csv_name)
    time.sleep(SEARCH_DELAY)

    web_signals = {}
    if web_results:
        web_signals = extract_web_signals(client, email, csv_name, web_results)

    web_linkedin = web_signals.get("linkedin_url", "")
    web_handle = web_signals.get("handle", "")
    evidence = web_signals.get("evidence", "")

    # Step 2: Find LinkedIn
    match = None

    # 2a: If web search found a LinkedIn URL directly, validate it
    if web_linkedin and "linkedin.com/in/" in web_linkedin:
        match = validate_linkedin_url(client, web_linkedin, csv_name, email, handle)
        if match and not match.get("linkedin_url"):
            match = None  # Validation rejected it

    # 2b: If web search found a handle, try linkedin.com/in/{handle}
    if not match and web_handle:
        candidate_url = f"https://www.linkedin.com/in/{web_handle}"
        match = validate_linkedin_url(client, candidate_url, csv_name, email, handle)
        if match and not match.get("linkedin_url"):
            match = None

    # 2c: Search LinkedIn engine: handle first, then csv_name
    if not match:
        match = search_and_rank_linkedin(
            client, csv_name, email, handle, web_signals, context,
        )

    # Build output
    with _progress_lock:
        _progress_count += 1
        cnt = _progress_count

    if match and match.get("linkedin_url"):
        url = match["linkedin_url"]
        with _progress_lock:
            _found_count += 1
        confidence = match.get("confidence", "")

        result_row["LinkedIn_URL"] = url
        result_row["LinkedIn_Snippet"] = match.get("snippet", "")
        result_row["LinkedIn_Title"] = match.get("linkedin_title", "")
        result_row["LinkedIn_Company"] = match.get("linkedin_company", "")
        result_row["Confidence"] = confidence
        result_row["Confidence_Reason"] = match.get("confidence_reason", "")
        result_row["Web_Evidence"] = evidence
        print(f"[{cnt}/{_total_to_process}] {email} ({csv_name or handle}) -> {url} | conf={confidence}", flush=True)
    else:
        reason = "No match found"
        if match:
            reason = match.get("confidence_reason", reason)
        search_worked = web_results is not None
        result_row["LinkedIn_URL"] = ""
        result_row["LinkedIn_Snippet"] = ""
        result_row["LinkedIn_Title"] = ""
        result_row["LinkedIn_Company"] = ""
        result_row["Confidence"] = "None" if search_worked else ""
        result_row["Confidence_Reason"] = reason
        result_row["Web_Evidence"] = evidence
        if search_worked:
            print(f"[{cnt}/{_total_to_process}] {email} ({csv_name or handle}) -> not found", flush=True)
        else:
            print(f"[{cnt}/{_total_to_process}] {email} ({csv_name or handle}) -> search failed (will retry)", flush=True)

    return result_row


# ── I/O ──────────────────────────────────────────────────────────────────────


def load_existing_results(output_file: str, email_column: str) -> dict:
    """Load already-enriched results for resume support.

    Only treat a row as 'done' if it has a LinkedIn URL or a Confidence
    value (meaning Gemini actually evaluated results and found no match).
    Rows with all-empty enrichment fields are treated as not-yet-processed
    so they get retried on the next run.
    """
    results = {}
    if os.path.exists(output_file):
        with open(output_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                email = row.get(email_column, "").strip().lower()
                has_url = row.get("LinkedIn_URL", "").startswith("http")
                has_confidence = row.get("Confidence", "")
                if email and (has_url or has_confidence):
                    results[email] = row
    return results


def write_output(all_rows: list, results: dict, fieldnames: list, output_file: str, email_column: str):
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in all_rows:
            email = row[email_column].strip().lower()
            if email in results:
                writer.writerow(results[email])
            else:
                writer.writerow(row)


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    global _total_to_process

    parser = argparse.ArgumentParser(
        description="Enrich personal email addresses to LinkedIn profiles (Web Search + LinkedIn Search + Gemini)"
    )
    parser.add_argument("--input", required=True, help="Input CSV path")
    parser.add_argument("--output", default="", help="Output CSV path (default: {input_stem}_enriched.csv)")
    parser.add_argument("--email-column", default="Email", help="Column with email addresses")
    parser.add_argument("--name-column", default="", help="Column with person names (optional)")
    parser.add_argument("--context", default="", help="Context for Gemini prompt")
    parser.add_argument("--skip-value", default="", help="Row value to skip (e.g. 'Anonymous user')")
    parser.add_argument("--test", type=int, default=0, help="Process only first N rows")
    parser.add_argument("--workers", type=int, default=5, help="Parallel threads")
    parser.add_argument("--checkpoint", type=int, default=50, help="Save every N rows")
    args = parser.parse_args()

    output_file = args.output
    if not output_file:
        stem = Path(args.input).stem
        output_file = str(Path(args.input).parent / f"{stem}_enriched.csv")

    client = init_gemini()
    _get_access_token()
    print(f"Model: {GEMINI_MODEL} | Workers: {args.workers}", flush=True)
    print(f"Web engine: {VERTEX_WEB_ENGINE}", flush=True)
    print(f"LinkedIn engine: {VERTEX_LI_ENGINE}", flush=True)
    print(f"Input: {args.input}", flush=True)
    print(f"Output: {output_file}", flush=True)

    with open(args.input, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        input_fieldnames = list(reader.fieldnames)
        all_rows = list(reader)

    print(f"Total rows: {len(all_rows)}", flush=True)

    fieldnames = list(input_fieldnames)
    enrich_cols = [
        "LinkedIn_URL", "LinkedIn_Snippet", "LinkedIn_Title", "LinkedIn_Company",
        "Confidence", "Confidence_Reason", "Web_Evidence",
    ]
    for col in enrich_cols:
        if col not in fieldnames:
            fieldnames.append(col)

    for row in all_rows:
        for col in enrich_cols:
            row.setdefault(col, "")

    # Filter and deduplicate
    seen_emails = set()
    to_process_rows = []
    skipped_value = 0

    for row in all_rows:
        email = row.get(args.email_column, "").strip().lower()
        if not email or email in seen_emails:
            continue

        if args.skip_value and args.name_column:
            name = row.get(args.name_column, "").strip()
            if name == args.skip_value:
                skipped_value += 1
                continue

        seen_emails.add(email)
        to_process_rows.append(row)

    if skipped_value:
        print(f"Skipped (skip-value '{args.skip_value}'): {skipped_value}", flush=True)

    # Resume support
    existing = load_existing_results(output_file, args.email_column)
    print(f"Already enriched (in output): {len(existing)}", flush=True)

    to_process = [r for r in to_process_rows if r[args.email_column].strip().lower() not in existing]

    if args.test > 0:
        to_process = to_process[:args.test]
        print(f"TEST MODE: processing {len(to_process)} emails", flush=True)

    _total_to_process = len(to_process)
    print(f"To process: {_total_to_process}", flush=True)

    if not to_process:
        print("Nothing to process!")
        if not os.path.exists(output_file):
            write_output(all_rows, existing, fieldnames, output_file, args.email_column)
        return

    results = dict(existing)
    start_time = time.time()
    processed_since_checkpoint = 0

    def _worker(row):
        email = row[args.email_column].strip().lower()
        return email, enrich_one(row, client, args.email_column, args.name_column, args.context)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_worker, r): r[args.email_column] for r in to_process}

        for future in as_completed(futures):
            try:
                email, result = future.result()
                results[email] = result
                processed_since_checkpoint += 1

                if processed_since_checkpoint >= args.checkpoint:
                    write_output(all_rows, results, fieldnames, output_file, args.email_column)
                    elapsed = time.time() - start_time
                    done = _progress_count
                    rate = done / elapsed if elapsed > 0 else 0
                    remaining = (_total_to_process - done) / rate if rate > 0 else 0
                    print(
                        f"  -- checkpoint ({done}/{_total_to_process}), "
                        f"{rate:.1f}/s, ETA {remaining/60:.1f}m --",
                        flush=True,
                    )
                    processed_since_checkpoint = 0
            except Exception as e:
                email = futures[future]
                print(f"  Error enriching {email}: {e}", flush=True)

    write_output(all_rows, results, fieldnames, output_file, args.email_column)

    elapsed = time.time() - start_time
    found = sum(1 for r in results.values() if r.get("LinkedIn_URL", "").startswith("http"))
    total_enriched = len(results)

    print(f"\nDone! {_total_to_process} emails in {elapsed/60:.1f} minutes", flush=True)
    if total_enriched:
        print(f"LinkedIn profiles found: {found}/{total_enriched} ({found/total_enriched*100:.0f}% of processed)", flush=True)


if __name__ == "__main__":
    main()
