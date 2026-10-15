Deploy-only copy of the private sewasetu-assistant repo (Render). Synthetic data only.

# sewasetu-assistant

Two MCP servers (citizen, block officer) over the Sewa Setu old age pension portal, plus an eval harness.
The original portal README is in `PORTAL-README.md`.

| URL path | What |
|---|---|
| `/` , `/status`, `/admin` | the portal (Flask), unchanged apart from the changes listed below |
| `/mcp/citizen` | citizen MCP server (OAuth scope `citizen`, audience = this URL) |
| `/mcp/officer` | officer MCP server (OAuth scope `officer`) |
| `/eval-dashboard` | the invented people, conversations, checks (`/eval-dashboard.json` raw) |
| `/.well-known/oauth-protected-resource/mcp/{citizen,officer}` | RFC 9728 metadata |

Connect Claude Code: `claude mcp add --transport http sewasetu-citizen <BASE>/mcp/citizen`, then `/mcp` to sign in
(mobile + OTP; the OTP appears at `<BASE>/__gateway/`). Officer: `.../mcp/officer`, sign in with the departmental login.

## Run
```
pip install -r requirements.txt
ADMIN_PASSWORD=... PUBLIC_BASE_URL=https://your-host python deploy_start.py   # embedded Postgres + everything, port 7860
BASE=https://your-host ADMIN_PASSWORD=... DB_HOST=... python -m evals.run_evals   # needs DB access for the checks
```
`docker-compose.yml` from the handout still works for the portal alone.

## Changes to the portal (also in git history)
- `DB_*`, `SMS_GATEWAY_URL`, `UPLOAD_DIR`, `LOG_DIR` environment overrides.
- `validate_application`: gender and marital status must be one of the form's options.
- `scripts/deemed_approval.py`: does not deem-approve applicants who were under 60 at submission.
- New table `agent_drafts` (citizen drafts, 2h) and `agent_eval_runs`, created at startup.

## What is not here
No language model is in the eval loop; scripted personas test the servers' rules. See `DESIGN.md`.
