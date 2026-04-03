# GTM Toolkit

GTM (Go-to-Market) skills for AI agents: list building, data enrichment, and prospect research.

Skills follow the [Agent Skills](https://agentskills.io/specification) open standard and work with Claude Code, Codex, and any compatible agent.

## Installation

### Claude Code (plugin)

```bash
claude plugin add gtm-toolkit
```

### Codex + Claude Code (symlinks)

```bash
git clone <this-repo> ~/skills/gtm-toolkit
cd ~/skills/gtm-toolkit
./install.sh
```

This symlinks all skills into `~/.claude/skills/` and `~/.agents/skills/`.

## Skills

### api-scraper

Scrape data from websites by reverse-engineering their frontend API calls.

**Triggers:** "Scrape all X from [URL]", "Fetch data from [URL]", "Extract all [items] from [URL]"

**Example:**
```
Scrape all YC companies from https://www.ycombinator.com/companies
```

**Requires:** Chrome DevTools MCP (see below)

### linkedin-profile-enrich

Find LinkedIn profiles for a list of people in a CSV using Vertex AI Search + Gemini Flash ranking.

**Triggers:** "Find LinkedIn profiles for this CSV", "Enrich this list with LinkedIn URLs"

**Requires:** GCP Application Default Credentials, Vertex AI Search engine indexed on linkedin.com

## Requirements

### Chrome DevTools MCP (for api-scraper)

1. Use Chrome M144+ (Beta or newer)
2. Enable remote debugging at `chrome://inspect/#remote-debugging`
3. Add to your MCP config (`~/.claude.json`):

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

## Roadmap

- [x] api-scraper
- [x] linkedin-profile-enrich
- [ ] Email finder
- [ ] CRM export workflows
- [ ] Clay-like waterfall enrichment

## License

MIT
