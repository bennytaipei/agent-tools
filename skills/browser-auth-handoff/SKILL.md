---
name: browser-auth-handoff
description: >
  When an HTTP request, curl, API call, or page for a service a human
  can sign into returns 401 or 403, redirects to login or SSO, or shows
  a sign-in form, open that service in the agent browser and hand off
  sign-in instead of reporting the task blocked. Use on authentication
  walls, login pages, unauthenticated API responses, and SSO redirects.
  On OMP, keep the built-in browser for quick unauthenticated browsing
  unless the prompt says "agent browser".
---

# Browser auth handoff

A 401, a 403, a redirect to a login or SSO page, or a sign-in form on a service a human can sign into is a handoff.

1. Do not report the task blocked.
2. Open the service's web UI with `navigate` (Grok) or `mcp__agent_browser_navigate` (OMP). Pass the page URL, not the API URL that returned 401 or 403. Which runtime to use is under "Which browser".
3. Call handoff and wait for it to return.
   - Grok tool: `handoff` on the `browser` server. OMP tool: `mcp__agent_browser_handoff`.
   - `reason` says what the person has to finish, in plain language.
   - `timeout` is seconds, at least 1, and long enough for a person to sign in. This input is the tool wait. An MCP client timeout is a different setting.
   - Pass `done_when` for the page after sign-in. Prefer `url_matches` on a post-login path. Never use a selector or text that exists on the login page. Use one object:
     - `url_matches`: a regular expression matched against the page URL without the query string or fragment
     - `selector_present`: a CSS selector for a visible element on the page after sign-in
     - `text_present`: visible text on the page after sign-in
4. When `status` is `done`, continue the original task. Read screenshots, then click, type, scroll, select, and wait as the task requires.
   - Grok tools: `read`, `screenshot`, `click`, `type`, `scroll`, `select`, `wait_for`.
   - OMP tools: `mcp__agent_browser_read`, `mcp__agent_browser_screenshot`, `mcp__agent_browser_click`, `mcp__agent_browser_type`, `mcp__agent_browser_scroll`, `mcp__agent_browser_select`, `mcp__agent_browser_wait_for`.
5. When `status` is `timeout` or `cancelled`, report that to the user and stop. Do not open another handoff for the same wall.

## Which browser

On OMP, the built-in browser is the default for quick unauthenticated browsing. A prompt that contains "agent browser" uses this runtime (`mcp__agent_browser_*`) instead. An authentication wall always uses this runtime, because only it can hand the live page to a person.

On Grok, this runtime is the `browser` server.
