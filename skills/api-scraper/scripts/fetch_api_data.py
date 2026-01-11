#!/usr/bin/env python3
"""
Generic API data fetcher script.
This script is used by the api-scraper skill to fetch data from discovered APIs.

Usage:
    python fetch_api_data.py --url URL [--method METHOD] [--headers HEADERS_JSON]
                             [--payload PAYLOAD_JSON] [--output OUTPUT_FILE]
                             [--format FORMAT] [--paginate]
"""

import argparse
import csv
import json
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


def fetch_data(url, method="GET", headers=None, payload=None):
    """
    Fetch data from an API endpoint.

    Args:
        url: The API endpoint URL
        method: HTTP method (GET or POST)
        headers: Dict of HTTP headers
        payload: Dict payload for POST requests

    Returns:
        Parsed JSON response
    """
    headers = headers or {}
    headers.setdefault("Content-Type", "application/json")

    data = None
    if payload and method == "POST":
        data = json.dumps(payload).encode('utf-8')

    req = Request(url, data=data, headers=headers, method=method)

    try:
        with urlopen(req) as response:
            return json.loads(response.read().decode('utf-8'))
    except HTTPError as e:
        error_body = e.read().decode('utf-8')
        print(f"HTTP Error {e.code}: {error_body}", file=sys.stderr)
        raise
    except URLError as e:
        print(f"URL Error: {e.reason}", file=sys.stderr)
        raise


def fetch_with_pagination(url, method="GET", headers=None, payload=None,
                          page_param="page", limit_param="limit", limit=100,
                          results_key="hits", total_key="nbHits", delay=0.1):
    """
    Fetch all data with pagination support.

    Args:
        url: Base API URL
        method: HTTP method
        headers: HTTP headers dict
        payload: Base payload dict (for POST)
        page_param: Name of page parameter
        limit_param: Name of limit parameter
        limit: Items per page
        results_key: Key in response containing results array
        total_key: Key in response containing total count
        delay: Delay between requests in seconds

    Returns:
        List of all fetched items
    """
    all_items = []
    page = 0
    total = None

    while True:
        # Build request with pagination
        if method == "POST":
            current_payload = (payload or {}).copy()
            current_payload[page_param] = page
            current_payload[limit_param] = limit
            result = fetch_data(url, method, headers, current_payload)
        else:
            sep = "&" if "?" in url else "?"
            paginated_url = f"{url}{sep}{page_param}={page}&{limit_param}={limit}"
            result = fetch_data(paginated_url, method, headers)

        # Extract results
        if isinstance(result, list):
            items = result
        elif results_key and results_key in result:
            items = result[results_key]
        else:
            items = result.get("data", result.get("items", result.get("results", [])))

        if not items:
            break

        all_items.extend(items)

        # Check if we've fetched all items
        if total_key and total_key in result:
            total = result[total_key]
            print(f"Fetched page {page + 1}: {len(items)} items (Total: {len(all_items)}/{total})", file=sys.stderr)
            if len(all_items) >= total:
                break
        else:
            print(f"Fetched page {page + 1}: {len(items)} items (Total: {len(all_items)})", file=sys.stderr)
            if len(items) < limit:
                break

        page += 1
        time.sleep(delay)

    return all_items


def flatten_dict(d, parent_key='', sep='_'):
    """Flatten nested dict for CSV export."""
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep).items())
        elif isinstance(v, list):
            items.append((new_key, json.dumps(v)))
        else:
            items.append((new_key, v))
    return dict(items)


def save_json(data, output_file):
    """Save data as JSON."""
    with open(output_file, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"Saved {len(data)} items to {output_file}", file=sys.stderr)


def save_csv(data, output_file):
    """Save data as CSV (flattened)."""
    if not data:
        print("No data to save", file=sys.stderr)
        return

    # Flatten all items
    flattened = [flatten_dict(item) if isinstance(item, dict) else {"value": item} for item in data]

    # Get all unique keys
    all_keys = set()
    for item in flattened:
        all_keys.update(item.keys())
    fieldnames = sorted(all_keys)

    with open(output_file, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(flattened)

    print(f"Saved {len(data)} items to {output_file}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Fetch data from an API endpoint")
    parser.add_argument("--url", required=True, help="API endpoint URL")
    parser.add_argument("--method", default="GET", choices=["GET", "POST"], help="HTTP method")
    parser.add_argument("--headers", help="JSON string of headers")
    parser.add_argument("--payload", help="JSON string of request payload")
    parser.add_argument("--output", "-o", help="Output file path")
    parser.add_argument("--format", "-f", default="json", choices=["json", "csv"], help="Output format")
    parser.add_argument("--paginate", action="store_true", help="Enable pagination")
    parser.add_argument("--page-param", default="page", help="Pagination page parameter name")
    parser.add_argument("--limit-param", default="hitsPerPage", help="Pagination limit parameter name")
    parser.add_argument("--limit", type=int, default=100, help="Items per page")
    parser.add_argument("--results-key", default="hits", help="Key in response containing results")
    parser.add_argument("--total-key", default="nbHits", help="Key in response containing total count")
    parser.add_argument("--delay", type=float, default=0.1, help="Delay between paginated requests")

    args = parser.parse_args()

    # Parse headers and payload
    headers = json.loads(args.headers) if args.headers else None
    payload = json.loads(args.payload) if args.payload else None

    # Fetch data
    if args.paginate:
        data = fetch_with_pagination(
            args.url, args.method, headers, payload,
            page_param=args.page_param,
            limit_param=args.limit_param,
            limit=args.limit,
            results_key=args.results_key,
            total_key=args.total_key,
            delay=args.delay
        )
    else:
        data = fetch_data(args.url, args.method, headers, payload)
        # If result is not a list, try to extract the data array
        if isinstance(data, dict):
            data = data.get(args.results_key, data.get("data", data.get("items", [data])))

    # Output
    if args.output:
        if args.format == "csv":
            save_csv(data, args.output)
        else:
            save_json(data, args.output)
    else:
        print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
