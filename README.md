# GTM Toolkit

A Claude Code plugin for GTM (Go-to-Market) workflows: list building, data enrichment, and prospect research.

## Installation

```bash
claude plugin add gtm-toolkit
```

## Skills

### api-scraper

Scrape data from websites by reverse-engineering their frontend API calls.

**Trigger phrases:**
- "Scrape all companies from [URL]"
- "Fetch data from [URL]"
- "Extract all [items] from [URL]"
- "Get all X from [website]"

**Example:**
```
Scrape all YC companies from https://www.ycombinator.com/companies
```

**Output:** JSON or CSV file with extracted data.

## Requirements

### Chrome DevTools MCP

This plugin requires Chrome DevTools MCP for browser automation and API inspection.

**Setup:**

1. Use Chrome M144+ (Beta or newer)
2. Enable remote debugging at `chrome://inspect/#remote-debugging`
3. Add to your Claude Code MCP config (`~/.claude.json`):

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

4. Authorize Claude when prompted

**Note:** Uses your existing browser session - you stay logged in to all your sites.

## Roadmap

- [ ] LinkedIn enrichment
- [ ] Company data enrichment
- [ ] Email finder
- [ ] CRM export workflows
- [ ] Clay-like waterfall enrichment

## License

MIT
