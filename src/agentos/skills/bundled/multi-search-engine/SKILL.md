---
name: multi-search-engine
description: "Query the web through multiple search engines (DuckDuckGo, Brave, Tavily, SerpAPI, and X/Twitter via xAI x_search) with a single CLI surface. Trigger when the user asks for a research search, fact lookup, source discovery, current discussion on X, or wants to compare engines for coverage. The skill aggregates per-engine result lists and normalizes them into a uniform JSON shape for downstream skills (deep-research is the primary consumer). `--engines auto` (the default) runs DuckDuckGo plus every engine whose API key or xAI credential is present; a requested engine without its key records a per-engine error and the run continues."
homepage: ""
provenance:
  origin: clawhub-mit0
  license: MIT-0
  upstream_url: https://clawhub.ai/multi-search-engine
  maintained_by: AgentOS
metadata:
  {
    "platform":
      {
        "emoji": "🔍",
        "requires": { "anyBins": ["python", "python3"] },
      },
  }
entrypoint:
  command: "{python} {baseDir}/scripts/search.py"
  args:
    - --query
    - "{{ with.query | default(inputs.user_message) }}"
    - --engines
    - "{{ with.engines | default(['auto']) | join(',') }}"
    - --limit
    - "{{ with.max_results | default(25) }}"
    - --json
  parse: json
  timeout: 200
---

# multi-search-engine

A unified CLI for querying several web search engines in parallel and
returning a normalized result list. Built on `httpx` and `beautifulsoup4`
(both already in AgentOS default dependencies, so no extra install
beyond `pip install use-agent-os`).

## Use cases

- Building a `deep-research` round with diverse engine coverage
- Fact-check a claim against >1 engine
- Compare what Brave returns vs DuckDuckGo for the same query
- Pull current discussion, reactions, or claims on X alongside web results

## Limitations

- A single engine sufficient → call its API directly instead
- Need headless-browser DOM rendering → this skill is HTTP-only

## Quick start

```bash
{python} {baseDir}/scripts/search.py \
    --query "openclaw skill registry" \
    --engines auto \
    --limit 10 \
    --json
```

Output:

```json
{
  "query": "...",
  "engines": ["duckduckgo", "x"],
  "results": [
    {
      "engine": "duckduckgo",
      "title": "...",
      "url": "https://...",
      "snippet": "...",
      "rank": 1
    },
    {
      "engine": "x",
      "title": "https://x.com/someone/status/123",
      "url": "https://x.com/someone/status/123",
      "snippet": "",
      "rank": 1
    }
  ],
  "answers": [
    {"engine": "x", "text": "Synthesized answer from xAI, grounded in the cited posts."}
  ],
  "errors": [
    {"engine": "brave", "reason": "BRAVE_SEARCH_API_KEY/BRAVE_API_KEY not set; skipping"},
    {"engine": "duckduckgo", "reason": "DuckDuckGo bot challenge (HTTP 202) — rate-limited; retry later or use another engine"}
  ]
}
```

`engines` is the list that actually ran after `auto` expansion. `answers`
carries the synthesized text that answer-style engines (`x`) return in
addition to their citations; page-style engines never populate it.

## Engines

| Engine | Needs key | Key env var | Strength |
|---|---|---|---|
| `duckduckgo` | no | — | Privacy-friendly baseline; the only keyless engine. Rate-limits with an HTTP 202 challenge page, which the script reports as an error (after one retry) instead of an empty result |
| `brave` | yes | `BRAVE_SEARCH_API_KEY` or legacy `BRAVE_API_KEY` | High-quality results, generous free tier, recency filter |
| `tavily` | yes | `TAVILY_API_KEY` | Designed for AI agents, returns clean JSON |
| `serpapi` | yes | `SERPAPI_API_KEY` | Google results via an aggregator; paid |
| `x` | yes | xAI login (`agentos auth login xai`) or `XAI_API_KEY` | Posts and threads on X via xAI's server-side `x_search`; returns an answer plus citations. Slow (60–120s) and billed to your xAI account, not through `agentos cost` — see `docs/x-search.md` |

`--engines auto` (default) expands to `duckduckgo` plus each of `brave`,
`tavily`, `serpapi` whose key is set, plus `x` when an xAI credential is
available. Explicit names can be mixed in (`--engines auto,brave`), and
duplicates are dropped. With no keys configured, `auto` is DuckDuckGo only.

The `x` engine reads the OAuth access token the gateway stores in
`~/.agentos/auth.json` (`AGENTOS_AUTH_STORE` / `AGENTOS_STATE_DIR` honoured)
and never refreshes it — refresh is the gateway's job. An expired login with
no `XAI_API_KEY` is reported as an error, not silently skipped. Override the
model with `AGENTOS_X_SEARCH_MODEL` (default `grok-4.5`).

The script never errors out when an API-key engine's key is missing — it
records a per-engine `errors` entry and continues with the rest. Pass
`--strict` to fail fast when any requested engine is unavailable.

## Routing tips

- Default → `auto`; it already picks everything that can run here
- Question is about what people are saying on X → make sure `x` is in the
  list (`auto` includes it whenever a credential exists); read `answers`
  for the synthesis and `results` for the posts to cite
- Time-sensitive (last 24h) → `brave` (recency filter) or `tavily`; `x`
  for breaking discussion
- Long-tail academic → fall back to direct arXiv / Google Scholar; this
  skill targets general web search
- Pure web query where cost matters → `--engines duckduckgo` skips the
  xAI call entirely

`engines.md` has the full per-engine guidance.

## Boundaries

- HTTP-only. JS-rendered pages will not be readable; use a headless-browser
  skill if needed.
- DuckDuckGo is the only HTML-scraping engine and is best-effort — HTML
  structure changes break it. Parse failures and bot challenges are
  recorded per engine and the run keeps going. Other keyless engines
  (Bing, Brave HTML, Mojeek) were evaluated and rejected: they either block
  non-browser clients outright or return poisoned, irrelevant results.
- Rate limiting is not handled inside the script beyond one retry on a
  DuckDuckGo challenge. Calling the same engine 10x/sec from a loop will
  get blocked. Add jitter and back-off in the caller.
- Captcha-protected results are not bypassed.
- `x` cannot post, reply, or read an authenticated X account; it is search
  only.
