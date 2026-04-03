#!/usr/bin/env python3 -u
"""
Enrich a CSV of people with LinkedIn profile URLs using
Vertex AI Search + Gemini Flash ranking.

Two-pass search with parallel execution and checkpoint/resume.

Usage:
    python3 linkedin_profile_enrich.py \
      --input people.csv \
      --output people_enriched.csv \
      --name-column Name \
      --hint-column Company \
      --context "Speaker at an HR technology conference" \
      --test 10
"""

import csv
import json
import os
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

from json_extraction import extract_and_parse_json

# ── API config (hardcoded defaults — our GCP setup) ─────────────────────────
GEMINI_MODEL = "gemini-2.5-flash"
VERTEX_LOCATION = "us-central1"
GOOGLE_CLOUD_PROJECT = "ornate-acronym-372603"

VERTEX_SEARCH_PROJECT = "640418593024"
VERTEX_SEARCH_ENGINE = "syft-li_1772176028063"
VERTEX_SEARCH_URL = (
    f"https://discoveryengine.googleapis.com/v1alpha/projects/{VERTEX_SEARCH_PROJECT}"
    f"/locations/global/collections/default_collection"
    f"/engines/{VERTEX_SEARCH_ENGINE}/servingConfigs/default_search:search"
)

SEARCH_DELAY = 0.3  # seconds between Vertex AI Search calls

# ── Thread-safe progress ─────────────────────────────────────────────────────
_progress_lock = Lock()
_progress_count = 0
_total_to_process = 0
_found_count = 0

# ── Auth ─────────────────────────────────────────────────────────────────────
_auth_lock = Lock()
_credentials = None
_token_expiry = 0


def _get_access_token() -> str:
    """Get a valid OAuth2 access token, refreshing if needed. Thread-safe."""
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


# ── Name parsing ─────────────────────────────────────────────────────────────


def parse_name(raw_name: str, name_format: str) -> str:
    """Parse name based on format. Handles credential suffixes for last,first."""
    raw_name = raw_name.strip().strip('"')
    if name_format == "last,first" and "," in raw_name:
        parts = raw_name.split(",", 1)
        last = parts[0].strip()
        first = parts[1].strip()
        # Handle suffixes like "Hummel, CRP, GMS-T, LaMonica"
        if len(parts[1].split(",")) > 1:
            sub_parts = [p.strip() for p in parts[1].split(",")]
            first = sub_parts[-1]
        return f"{first} {last}"
    return raw_name


# ── Vertex AI Search ────────────────────────────────────────────────────────


def vertex_search(query: str) -> Optional[list]:
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
                VERTEX_SEARCH_URL,
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
    """Keep only results whose link contains linkedin.com/in/."""
    return [item for item in items if "linkedin.com/in/" in item.get("link", "")]


# ── Gemini ranking ──────────────────────────────────────────────────────────


def rank_with_gemini(
    client: genai.Client, name: str, hint: str, context: str, linkedin_items: list
) -> Optional[dict]:
    """Send LinkedIn results to Gemini Flash; return best match as dict."""
    results_for_prompt = []
    for i, item in enumerate(linkedin_items):
        results_for_prompt.append({
            "position": i + 1,
            "url": item.get("link", ""),
            "title": item.get("title", ""),
            "snippet": item.get("snippet", ""),
        })

    hint_line = f"Hint: {hint}" if hint else "Hint: (none provided)"
    context_line = context if context else ""

    prompt = f"""LinkedIn profile matching. Target: {name}
{hint_line}
{context_line}

Results: {json.dumps(results_for_prompt)}

Instructions: Pick the best match. Consider how well the name matches.
If hint is provided, prefer profiles matching that company/title.

Return ONLY valid JSON:
{{"linkedin_url": "<url>", "linkedin_title": "<title from snippet>", "linkedin_company": "<company or Unknown>", "snippet": "<snippet verbatim>", "confidence": "High|Medium|Low", "confidence_reason": "<brief explanation>"}}"""

    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL, contents=prompt
            )
            text = (response.text or "").strip()
            result = extract_and_parse_json(text)
            if result and result.get("linkedin_url"):
                return result
            if result is not None:
                return None
            if attempt < 2:
                time.sleep(1)
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                print(f"  Gemini error: {e}", flush=True)
    return None


# ── Per-person enrichment ──────────────────────────────────────────────────


def enrich_one(
    row: dict,
    client: genai.Client,
    name_column: str,
    name_format: str,
    hint_column: str,
    context: str,
    pass2_keywords: str,
    target_titles: list,
) -> dict:
    """Enrich a single person. Returns updated row dict."""
    global _progress_count, _found_count

    raw_name = row[name_column]
    display_name = parse_name(raw_name, name_format)
    hint = row.get(hint_column, "").strip() if hint_column else ""

    result_row = dict(row)

    # Pass 1: name + hint
    q1 = f"{display_name} {hint}" if hint else display_name
    items = vertex_search(q1)
    time.sleep(SEARCH_DELAY)

    search_succeeded = items is not None
    li_items = filter_linkedin_results(items or [])

    # Pass 2: fallback if no LinkedIn /in/ results
    if not li_items:
        if pass2_keywords:
            q2 = f"{display_name} {pass2_keywords}"
        elif hint:
            q2 = f"{display_name} LinkedIn profile {hint}"
        else:
            q2 = f"{display_name} LinkedIn profile"
        items2 = vertex_search(q2)
        time.sleep(SEARCH_DELAY)
        if items2 is not None:
            search_succeeded = True
        li_items = filter_linkedin_results(items2 or [])

    if not li_items:
        with _progress_lock:
            _progress_count += 1
            cnt = _progress_count
        if search_succeeded:
            result_row["LinkedIn_URL"] = ""
            result_row["LinkedIn_Snippet"] = ""
            result_row["LinkedIn_Title"] = ""
            result_row["LinkedIn_Company"] = ""
            result_row["Confidence"] = "None"
            result_row["Confidence_Reason"] = "No LinkedIn results found"
            if target_titles:
                result_row["Title_Match"] = ""
            print(f"[{cnt}/{_total_to_process}] {display_name} -> not found", flush=True)
        else:
            result_row["LinkedIn_URL"] = ""
            result_row["LinkedIn_Snippet"] = ""
            result_row["LinkedIn_Title"] = ""
            result_row["LinkedIn_Company"] = ""
            result_row["Confidence"] = ""
            result_row["Confidence_Reason"] = ""
            if target_titles:
                result_row["Title_Match"] = ""
            print(f"[{cnt}/{_total_to_process}] {display_name} -> search failed (will retry)", flush=True)
        return result_row

    # Gemini ranking
    match = rank_with_gemini(client, display_name, hint, context, li_items)

    if not match or not match.get("linkedin_url"):
        with _progress_lock:
            _progress_count += 1
            cnt = _progress_count
        result_row["LinkedIn_URL"] = ""
        result_row["LinkedIn_Snippet"] = ""
        result_row["LinkedIn_Title"] = ""
        result_row["LinkedIn_Company"] = ""
        result_row["Confidence"] = "None"
        result_row["Confidence_Reason"] = "Gemini found no confident match"
        if target_titles:
            result_row["Title_Match"] = ""
        print(f"[{cnt}/{_total_to_process}] {display_name} -> no match (Gemini)", flush=True)
        return result_row

    url = match["linkedin_url"]
    li_title = match.get("linkedin_title", "")
    li_company = match.get("linkedin_company", "")
    snippet = match.get("snippet", "")
    confidence = match.get("confidence", "")
    reason = match.get("confidence_reason", "")

    t_match = ""
    if target_titles:
        t_lower = li_title.lower()
        t_match = str(any(t in t_lower for t in target_titles))

    with _progress_lock:
        _progress_count += 1
        _found_count += 1
        cnt = _progress_count
    print(
        f"[{cnt}/{_total_to_process}] {display_name} -> {url} | conf={confidence}",
        flush=True,
    )

    result_row["LinkedIn_URL"] = url
    result_row["LinkedIn_Snippet"] = snippet
    result_row["LinkedIn_Title"] = li_title
    result_row["LinkedIn_Company"] = li_company
    result_row["Confidence"] = confidence
    result_row["Confidence_Reason"] = reason
    if target_titles:
        result_row["Title_Match"] = t_match
    return result_row


# ── I/O ─────────────────────────────────────────────────────────────────────


def load_existing_results(output_file: str, name_column: str) -> dict:
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
                name = row.get(name_column, "")
                has_url = row.get("LinkedIn_URL", "").startswith("http")
                has_confidence = row.get("Confidence", "")
                if name and (has_url or has_confidence):
                    results[name] = row
    return results


def write_output(all_rows: list, results: dict, fieldnames: list, output_file: str, name_column: str):
    with open(output_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in all_rows:
            name = row[name_column]
            if name in results:
                writer.writerow(results[name])
            else:
                writer.writerow(row)


# ── Main ────────────────────────────────────────────────────────────────────


def main():
    global _total_to_process

    parser = argparse.ArgumentParser(
        description="Enrich a CSV of people with LinkedIn profiles (Vertex AI Search + Gemini Flash)"
    )
    parser.add_argument("--input", required=True, help="Input CSV path")
    parser.add_argument("--output", default="", help="Output CSV path (default: {input_stem}_enriched.csv)")
    parser.add_argument("--name-column", default="Name", help="Column with person names")
    parser.add_argument("--name-format", default="first last", choices=["first last", "last,first"],
                        help="Name format: 'first last' or 'last,first'")
    parser.add_argument("--hint-column", default="", help="Column with hint info (company name or job title)")
    parser.add_argument("--linkedin-column", default="", help="Existing LinkedIn column — skip rows with URLs")
    parser.add_argument("--context", default="", help="Context sentence for Gemini prompt")
    parser.add_argument("--pass2-keywords", default="", help="Comma-separated keywords for Pass 2 fallback")
    parser.add_argument("--target-titles", default="", help="Comma-separated title keywords for Title_Match")
    parser.add_argument("--skip-value", default="", help="Row value to skip (e.g. 'Anonymous user')")
    parser.add_argument("--test", type=int, default=0, help="Process only first N names")
    parser.add_argument("--workers", type=int, default=5, help="Parallel threads")
    parser.add_argument("--checkpoint", type=int, default=50, help="Save every N rows")
    args = parser.parse_args()

    # Resolve output path
    output_file = args.output
    if not output_file:
        stem = Path(args.input).stem
        output_file = str(Path(args.input).parent / f"{stem}_enriched.csv")

    # Parse target titles
    target_titles = [t.strip().lower() for t in args.target_titles.split(",") if t.strip()] if args.target_titles else []

    # Parse pass2 keywords
    pass2_keywords = " ".join(k.strip() for k in args.pass2_keywords.split(",") if k.strip()) if args.pass2_keywords else ""

    # Init APIs
    client = init_gemini()
    _get_access_token()
    print(f"Model: {GEMINI_MODEL} | Workers: {args.workers} | Search: Vertex AI", flush=True)
    print(f"Input: {args.input}", flush=True)
    print(f"Output: {output_file}", flush=True)

    # Read input CSV
    with open(args.input, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        input_fieldnames = list(reader.fieldnames)
        all_rows = list(reader)

    print(f"Total rows: {len(all_rows)}", flush=True)

    # Build output fieldnames (preserve original + add enrichment columns)
    fieldnames = list(input_fieldnames)
    enrich_cols = ["LinkedIn_URL", "LinkedIn_Snippet", "LinkedIn_Title", "LinkedIn_Company",
                   "Confidence", "Confidence_Reason"]
    if target_titles:
        enrich_cols.append("Title_Match")
    for col in enrich_cols:
        if col not in fieldnames:
            fieldnames.append(col)

    # Ensure all rows have the enrichment columns
    for row in all_rows:
        for col in enrich_cols:
            row.setdefault(col, "")

    # Filter rows
    to_process_rows = []
    skipped_existing_url = 0
    skipped_value = 0
    seen_names = set()

    for row in all_rows:
        name = row.get(args.name_column, "").strip()
        if not name:
            continue

        # Skip specified values
        if args.skip_value and name == args.skip_value:
            skipped_value += 1
            continue

        # Skip rows that already have a LinkedIn URL in the input
        if args.linkedin_column:
            existing_url = row.get(args.linkedin_column, "").strip()
            if existing_url.startswith("http"):
                skipped_existing_url += 1
                continue

        # Deduplicate by name
        if name in seen_names:
            continue
        seen_names.add(name)

        to_process_rows.append(row)

    if skipped_existing_url:
        print(f"Skipped (existing LinkedIn URL): {skipped_existing_url}", flush=True)
    if skipped_value:
        print(f"Skipped (skip-value '{args.skip_value}'): {skipped_value}", flush=True)

    # Resume support — load existing results from output file
    existing = load_existing_results(output_file, args.name_column)
    print(f"Already enriched (in output): {len(existing)}", flush=True)

    to_process = [r for r in to_process_rows if r[args.name_column] not in existing]

    if args.test > 0:
        to_process = to_process[: args.test]
        print(f"TEST MODE: processing {len(to_process)} names", flush=True)

    _total_to_process = len(to_process)
    print(f"To process: {_total_to_process}", flush=True)

    if not to_process:
        print("Nothing to process!")
        if not os.path.exists(output_file):
            write_output(all_rows, existing, fieldnames, output_file, args.name_column)
        return

    # Parallel enrichment
    results = dict(existing)
    start_time = time.time()
    processed_since_checkpoint = 0

    def _worker(row):
        return row[args.name_column], enrich_one(
            row, client, args.name_column, args.name_format,
            args.hint_column, args.context, pass2_keywords, target_titles,
        )

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_worker, r): r[args.name_column] for r in to_process}

        for future in as_completed(futures):
            try:
                name, result = future.result()
                results[name] = result
                processed_since_checkpoint += 1

                if processed_since_checkpoint >= args.checkpoint:
                    write_output(all_rows, results, fieldnames, output_file, args.name_column)
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
                name = futures[future]
                print(f"  Error enriching {name}: {e}", flush=True)

    # Final save
    write_output(all_rows, results, fieldnames, output_file, args.name_column)

    elapsed = time.time() - start_time
    found = sum(1 for r in results.values() if r.get("LinkedIn_URL", "").startswith("http"))
    total_enriched = len(results)

    print(f"\nDone! {_total_to_process} names in {elapsed/60:.1f} minutes", flush=True)
    print(f"LinkedIn profiles found: {found}/{total_enriched} ({found/total_enriched*100:.0f}% of processed)" if total_enriched else "", flush=True)

    if target_titles:
        matched = sum(1 for r in results.values() if r.get("Title_Match") == "True")
        print(f"Title matches: {matched}", flush=True)


if __name__ == "__main__":
    main()
