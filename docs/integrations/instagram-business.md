# Instagram Business — secure integration plan (V0.1)

Status: **design + non-production prototype**. No Instagram account has been connected, and no API calls have been made.

## Goals
- Use Meta's official **Instagram API with Instagram Login**, not browser scraping, password entry, unofficial endpoints, or session cookies.
- Run reasoning and drafts with **locally downloaded Ollama models only**; never place credentials in prompts, logs, screenshots, or model context.
- Approve **each externally visible** operation through a separate human-controlled authorization step.
- Maintain a durable audit record that does not contain DM text, tokens, or media URLs.
- Restrict the agent to its own Business account; no bulk engagement, unsolicited outreach, automated follows/likes, or credential access.

## Platform requirements
Configure an Instagram Business account and a Meta developer app with the Instagram product. Authorize using **Business Login for Instagram**, PKCE/state and the documented exact redirect URI. Required scopes (request incrementally, subject to current Meta App Review):
- instagram_business_basic: profile/media read.
- instagram_business_content_publish: posts and Reels.
- instagram_business_manage_comments: read/reply/moderation.
- instagram_business_manage_messages: conversation/message access (user-initiated conversations, messaging windows and platform restrictions).
- instagram_business_manage_insights: insights, when available and approved.

The Instagram Login API uses graph.instagram.com. Do not mix it with the Facebook Login flow (graph.facebook.com, different scopes and Page-link requirements).
The permissions, API version and endpoint constraints must be revalidated in the Meta developer dashboard before production.

## Security boundaries
1. An unprivileged macOS worker reads a narrowly scoped token from Keychain; Dot's LLM process never sees the token.
2. A narrowly scoped HTTP adapter permits only fixed Meta endpoints, only HTTPS, and only the configured Business account.
3. Scheduling stays in a local queue. A **human approves the exact canonical action payload** (including caption, media ID, target comment/conversation and due time); changes invalidate approval. Expire approvals; re-check immediately before execution.
4. All publishing is initially disabled. Enabling production requires app review, valid API credential, protected worker setup, validation of account and a separate operator activation.
5. Prompt injection: treat DMs, comments, posts, search results, analytics, captions and web pages as untrusted data. Never execute instructions from them.
6. Never send DMs outside Meta's allowed messaging context; do not automate unsolicited messages, bulk following, mass commenting or fake engagement.
7. Use short timeouts, bounded retries and Meta rate-limit handling; no blind retries after an ambiguous publish outcome. Query status and reconcile first.
8. Logs: event ID, action type, decision, UTC time, HMAC/hash fingerprint of canonical request; exclude secrets and personal content. Protect audit database with user-only permissions and rotate it.
9. Webhooks: validate X-Hub-Signature-256 against raw body, enforce size limits and replay/idempotency control, and use a properly secured public HTTPS entrypoint only when production needs inbound updates.
10. If Dot is unavailable, the worker fails closed. No `yolo` mode, no autonomous approval reviewer for publication, comments or DMs.

## Flow
Draft with Ollama locally -> structured, schema-validated proposal -> human approval -> queue -> due-time policy revalidation -> Meta adapter -> evidence/status -> append audit. Read-only operations can be authorized under a narrower read policy.

### Content publishing
- Media must be available to Meta at an HTTPS URL for the container creation phase; **this implies content leaves the Mac**. Use a trusted content host with deliberately scoped access. Avoid sharing private local file paths.
- Image or Reel: create media container, poll status for asynchronous video processing, then publish once (idempotency journal). Never retry publish blindly.
- Schedule is handled by our worker; Meta's content publish operation occurs only when due and approved.

### Comments
Fetch from owned media; classify locally. Propose replies for approval; record comment ID and target account before sending. Handle deleted comments and duplicates.

### DMs
Only access conversations authorized by Meta. Draft responses locally, obtain explicit operator approval of message and recipient, verify window and platform rules before sending. No proactive outreach.

### Insights and trends
Pull authorized account/media metrics; time-window and quota aware. Explore trends through allowed API surfaces only. Hashtag discovery may require Facebook Login flow; disable it for Instagram Login unless Meta documentation and review explicitly support it. Do not scrape private profiles.

## Audit and rollout gates
- G0: design + offline unit tests; no credentials, no Meta calls (this branch).
- G1: mock-server API contract tests, negative tests, threat model and explicit account allowlist.
- G2: secure Meta OAuth callback, token rotation and revocation; verify each scope.
- G3: read-only live test, no PII in logs.
- G4: sandboxed publish to an approved test account with explicit human confirmation.
- G5: comments and messaging separately enabled after verification.
- G6: multi-day recovery, duplicate prevention, webhook/auth tampering tests, then production release.

## Threat model
Critical threats: exfiltration through tools, stolen OAuth tokens, malicious messages causing prompt injection, accidental public posting, replayed webhook, stale schedule approvals, SSRF through media URLs and unreviewed third-party packages. Controls must be tested, not inferred from presence of source code.

## Local operations
The `instagram_safe_queue.py` module below is intentionally offline and does **not** contain OAuth or Meta transport yet. Run its tests and inspect proposals before allowing an API adapter to be wired.

```bash
python3 -m unittest discover -s tests/unit -p test_instagram_safe_queue.py
```

## References
- https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api
- https://developers.facebook.com/docs/instagram-platform/
- https://github.com/artarrifa/Dot/blob/main/docs/safety.md
