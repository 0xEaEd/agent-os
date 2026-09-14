# Engine selection guide

Per-engine notes — when each is good, where it fails, and what an
appropriate query looks like.

## No-key engines (always available)

### DuckDuckGo

Implementation uses the HTML-form endpoint at `html.duckduckgo.com`.
Strengths: privacy-friendly, no rate limit at moderate volumes, returns a
mix of public-web sources without strong personalization. Weaknesses: less
recency-tuned than Brave; result ranking shifts week-to-week.

Rate limiting shows up as an HTTP 202 "anomaly" page rather than a 4xx. The
script retries once after a short pause and then records a
`DuckDuckGo bot challenge` error for the engine, so an empty `results` list
with an empty `errors` list genuinely means no organic hits. Redirect links
(`/l/?uddg=…`) are unquoted to the destination URL and sponsored `y.js` links
are dropped before `--limit` is applied.

Use when: general web search where you want a "neutral" baseline.

## API-key engines

### Brave Search API

`BRAVE_SEARCH_API_KEY` from <https://brave.com/search/api/>. Legacy
`BRAVE_API_KEY` is also accepted for migrated OpenClaw setups. 2k queries/month
free tier. Returns clean JSON with title, URL, description, and recency
hints.

Use when: building a deep-research pipeline that runs at scale; need
recency filtering.

### Tavily

`TAVILY_API_KEY` from <https://tavily.com>. Designed for AI agent
consumption — returns short summaries alongside results. Free tier
available.

Use when: the agent needs ready-to-use snippets rather than full source
HTML.

### SerpAPI

`SERPAPI_API_KEY` from <https://serpapi.com>. Aggregator that proxies
Google (the script requests `engine=google`), returning a uniform JSON
shape. Paid tiers; no free tier beyond a small credit.

Use when: Google parity matters and the project has the budget.

### Firecrawl

`FIRECRAWL_API_KEY` from <https://firecrawl.dev> — the same key `web_fetch`
uses to escalate JS-heavy pages, so an install that has one gets this engine
for free. The script calls `POST /v2/search` with `sources: [{type: "web"}]`
and **no `scrapeOptions`**, so each call bills the search credits only and
returns title, URL, and description without scraping every hit. `limit` is
capped at 100 and the query at 500 characters by the API.

Use when: you want a second API-backed web index next to Brave/Tavily, or
you already pay for Firecrawl and have no other key.

### X (xAI `x_search`)

Credential: the xAI OAuth login stored by `agentos auth login xai`
(`~/.agentos/auth.json`), or `XAI_API_KEY`. OAuth wins when both exist. The
script reads the stored access token and never refreshes it; the gateway
does that on its own `x_search` turns, so an expired token with no API key
is reported as an error.

The engine POSTs to xAI's Responses API with the server-side `x_search`
tool. Unlike the page engines it returns a synthesized **answer** (emitted
under the top-level `answers` key) plus **citations** to the posts it used
(emitted as `results` rows with `engine: "x"`, empty snippet, title falling
back to the URL when xAI only supplies a citation index). A complex query
runs 60–120s and is billed to the xAI account directly — it does not show
up in `agentos cost`.

Use when: the question is about current discussion, reactions, or claims
on X. Skip it (`--engines duckduckgo`) for a plain web lookup where the
xAI latency and cost buy nothing.

## Routing decision tree

```
Start with --engines auto
  = duckduckgo
  + brave / tavily / serpapi / firecrawl for each key that is set
  + x when an xAI login or XAI_API_KEY exists
Is the topic time-sensitive (last 24h)?
  yes → make sure brave or tavily is in the list; x for live discussion
Is it a plain web lookup and cost matters?
  yes → --engines duckduckgo (drops the xAI call)
Does it hinge on what people are saying on X?
  yes → x must be in the list; read `answers` first, cite `results`
```

## Per-engine result limits

Default `--limit 10` is safe across engines. Higher limits:

- DuckDuckGo: HTML returns up to ~30; beyond that, scrape the next page
- Brave: API tops out at 20 per request (the script clamps and logs)
- Tavily: 5 results on the free tier, 20 on paid
- SerpAPI: `num` up to 100, billed per request regardless
- Firecrawl: `limit` up to 100; results are metadata only unless you scrape
- X: `--limit` caps the citation rows; the answer text is not truncated

## Anti-patterns

- **Asking N engines in a tight loop without jitter**: rate limits will
  cascade. Sleep 200-500ms between requests, more for DuckDuckGo.
- **Trusting a single engine's top result as ground truth**: ranking is
  noisy. Cross-check with a second engine.
- **Requesting engines that are not implemented**: only `duckduckgo`,
  `brave`, `tavily`, `serpapi`, `firecrawl`, and `x` exist; anything else is recorded
  as `unknown engine`.
- **Running `x` on every trivial query**: it is the slowest and the only
  engine that spends money per call.

## Maintenance notes

DuckDuckGo is the only HTML scraper and breaks when the upstream site
reshuffles its CSS. Treat the parser as expected-to-fail-eventually code:
the script records parse failures and challenges per engine rather than
crashing, and the calling agent should fall back to another engine on the
spot. Routine maintenance is to test the parser against a known query
monthly. Keep the script's own `AgentOS-multi-search-engine` User-Agent:
on 2026-09-14 a browser-style Chrome UA was the one being served the 202
challenge while the honest UA passed. Other keyless engines were evaluated
on 2026-09-14 and rejected:
Bing serves poisoned, irrelevant results to non-browser clients (worse than
an empty list), Brave's HTML front end answers 429 with a captcha, and
Mojeek returns 403.
