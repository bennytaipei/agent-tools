## Browser tools

When any HTTP request, curl, API call, or page for a service a human
can sign into returns 401 or 403, redirects to a login or SSO page, or
shows a sign-in form, do not report the task blocked.

Open the service's web UI with the agent browser. On Grok, call
`navigate` on the `browser` server. On OMP, call
`mcp__agent_browser_navigate`. Use the page URL, not the API URL that
returned 401 or 403.

Call handoff and wait. On Grok the tool is `handoff`. On OMP it is
`mcp__agent_browser_handoff`. Pass a clear `reason`. Pass `done_when`
for the page after sign-in. Prefer `url_matches` on a post-login path.
`url_matches` is tested against the URL without the query string or
fragment. Never use `selector_present` or `text_present` for something
that already exists on the login page. Only visible elements count.
Pass `timeout` in seconds, at least 1. That is how long the tool waits
for the person.

When handoff returns `done`, continue the task. Read screenshots, then
click, type, and use the other browser tools (`read`, `screenshot`,
`click`, `type`, `scroll`, `select`, `wait_for` on Grok;
`mcp__agent_browser_*` on OMP). When handoff returns `timeout` or
`cancelled`, report that to the user and stop.

OMP's built-in browser stays the default for quick unauthenticated
browsing. The words "agent browser" in the prompt force this runtime.
An authentication wall always uses this runtime.
