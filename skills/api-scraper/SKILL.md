---
name: api-scraper
description: Scrape data from websites by inspecting and calling their frontend APIs. Use when asked to "scrape", "fetch data from", "extract data from", "get all X from" a website URL. Automatically discovers API endpoints, fetches data, and outputs JSON or CSV.
allowed-tools: Read, Write, Bash(python:*), Grep
---

# API Scraper

Scrape data from websites by reverse-engineering their frontend API calls.

**Requires:** Chrome DevTools MCP (`mcp__chrome-devtools__*`)

## Setup (One-time)

1. Use Chrome M144+ (Beta or newer)
2. Navigate to `chrome://inspect/#remote-debugging` and enable remote debugging
3. Add to your Claude Code MCP config:
   ```json
   {
     "mcpServers": {
       "chrome-devtools": {
         "command": "npx",
         "args": ["chrome-devtools-mcp@latest", "--autoConnect"]
       }
     }
   }
   ```
4. When prompted, authorize Claude to connect to your browser

**Note:** Uses your existing browser session - you stay logged in to all your sites.

## Workflow

### Step 1: Set up browser and navigate

```
1. Call mcp__chrome-devtools__new_page with the target URL
2. Wait for page to load (requests will be captured automatically)
```

### Step 2: List and filter network requests

```
1. Call mcp__chrome-devtools__list_network_requests with resourceTypes: ["fetch", "xhr"]
   - This filters to only API calls, excluding static assets
2. Look for data API calls by URL pattern:
   - /api/, /v1/, /v2/, /graphql
   - algolia.net, search, query
   - POST requests returning JSON
3. Note the reqid of interesting requests
```

### Step 3: Inspect API details

For each interesting request, call `mcp__chrome-devtools__get_network_request` with the reqid.

This returns the **full details**:
- **Request Headers** - Including auth tokens, API keys
- **Request Body** - The exact payload sent (for POST requests)
- **Response Headers** - Content-type, pagination info
- **Response Body** - The actual JSON data returned

Extract:
1. **Endpoint URL** - The API endpoint
2. **Authentication** - API keys in headers or URL params
3. **Request format** - How to structure the request body
4. **Response structure** - How to parse the data
5. **Pagination** - Total count, page info, cursors

### Step 4: Generate and run Python fetcher

Create a Python script using the exact request format discovered:

```python
#!/usr/bin/env python3
import json
import requests

# API configuration extracted from network inspection
API_URL = "extracted_url"
HEADERS = {
    "Content-Type": "application/json",
    # Add auth headers exactly as seen in request
}

def fetch_data():
    all_data = []
    # Use exact payload format from request body
    payload = {"requests": [...]}
    response = requests.post(API_URL, headers=HEADERS, json=payload)
    data = response.json()
    return data

if __name__ == "__main__":
    data = fetch_data()
    print(json.dumps(data, indent=2))
```

### Step 5: Output results

Save the fetched data:
- **JSON** (default): `{descriptive_name}.json`
- **CSV** (if requested): Use csv module to flatten and export

## Chrome DevTools MCP Tools

| Tool | Purpose |
|------|---------|
| `list_pages` | See open browser pages |
| `new_page` | Open URL in new page |
| `select_page` | Switch to a page |
| `navigate_page` | Navigate current page |
| `list_network_requests` | List captured requests (filter by type) |
| `get_network_request` | Get full request/response details |
| `evaluate_script` | Run JavaScript in page |
| `take_snapshot` | Get DOM snapshot |

## Filtering Network Requests

Use `resourceTypes` parameter to filter:

```
["fetch", "xhr"]     # API calls only (recommended)
["document"]         # HTML pages
["script"]           # JavaScript files
["stylesheet"]       # CSS files
```

## Example: YC Companies

```
1. mcp__chrome-devtools__new_page
   url: "https://www.ycombinator.com/companies"

2. mcp__chrome-devtools__list_network_requests
   resourceTypes: ["fetch", "xhr"]

   Result: reqid=229 POST https://45bwzj1sgc-dsn.algolia.net/...

3. mcp__chrome-devtools__get_network_request
   reqid: 229

   Result shows:
   - Request Body: {"requests":[{"indexName":"YCCompany_production",...}]}
   - Response Body: {"results":[{"nbHits":5611,"hits":[...],...}]}

4. Generate Python script with exact API format
5. Output: yc_companies.json
```

## Common API Patterns

### Algolia Search
```python
ALGOLIA_URL = "https://{app_id}-dsn.algolia.net/1/indexes/*/queries"
headers = {
    "Content-Type": "application/json",
}
# API key often in URL params: x-algolia-api-key=...
payload = {
    "requests": [{
        "indexName": "YCCompany_production",
        "params": "query=&hitsPerPage=1000"
    }]
}
```

### REST API with pagination
```python
page = 0
while True:
    response = requests.get(f"{API_URL}?page={page}&limit=100")
    data = response.json()
    if not data["items"]:
        break
    all_items.extend(data["items"])
    page += 1
```

### GraphQL
```python
query = """
query GetItems($first: Int, $after: String) {
    items(first: $first, after: $after) {
        edges { node { id, name } }
        pageInfo { hasNextPage, endCursor }
    }
}
"""
response = requests.post(API_URL, json={"query": query, "variables": {...}})
```

## Fallback: DOM Scraping

If no API is found (server-rendered pages like WordPress):

1. Use `mcp__chrome-devtools__evaluate_script` to extract data from DOM
2. Click "Load More" buttons via `mcp__chrome-devtools__click`
3. Use `mcp__chrome-devtools__take_snapshot` to get page structure

```javascript
// Example: Extract data from DOM
document.querySelectorAll('.card').forEach(card => {
    const name = card.querySelector('h3')?.textContent;
    const url = card.querySelector('a')?.href;
    // ...
});
```

## Tips

- **Filter by resourceTypes** - Use `["fetch", "xhr"]` to see only API calls
- **Check response body** - Contains actual data structure and pagination info
- **Copy exact headers** - Some APIs require specific headers to work
- **Look for total count** - Response often shows total items for pagination
- **Handle rate limits** - Add delays between requests if needed
