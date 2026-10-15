# Design note: Sewa Setu assistant

One page. The portal stays the system of record; the assistant is another way to apply and another
way to decide, held to the same rules.

## Shape
- `/mcp/citizen` and `/mcp/officer`: two separate streamable-HTTP MCP servers on the portal's own host.
  Different tool lists, different tokens. A citizen's assistant cannot even see `decide`.
- Sign-in is the portal's existing OAuth 2.1 server (PKCE, resource indicators, 2h citizen / 8h officer
  sessions). Each MCP server is only a resource server: it checks signature, issuer, audience (its own URL),
  role, and that the sign-in behind the token is still open. Discovery follows RFC 9728 so a client finds the
  sign-in page from the 401 alone. The assistant never sees an OTP or a password.
- Both servers call the portal's `validate_application()` / `create_application()` and write the same
  tables and audit log as the web form. An application made either way shows up in both.

## The three decisions that mattered most
1. **Identity is the signed-in mobile, never a tool argument.** No tool takes a mobile number. A grandson's
   phone can only act for the applicant who signed in with her own OTP, which is also how the portal knows
   she consented. Drafts, status, withdrawal and sign-out are all scoped to that mobile.
2. **The assistant prepares; a person confirms; the server enforces.** `review_draft` returns a read-back and a
   hash of exactly that content; `submit_application` needs that hash and the citizen's yes to the declaration.
   The officer side is the same: `decide` needs the officer's explicit instruction, a reason recorded against her
   name, refuses bulk work, refuses approval for an applicant under 60 at submission, and is closed to
   long-lived program tokens. Rules live in code the model cannot talk its way around.
3. **Evals changed the portal, not just the assistant.** Running invented people through both servers showed
   the nightly deemed-approval job approving under-60 applicants (637 such approvals already sit in the data),
   so it now leaves them for an officer. Details on `/eval-dashboard`.

## The one I would make differently with more time
Officers: the portal has a single departmental login, so every decision is recorded as `officer:admin` and
cannot be limited to one block. With more time I would add per-officer accounts and block assignments in
the portal and scope `list_queue`/`get_case`/`decide` to them. I would also run the personas with a real
language model in the loop (the harness is scripted today) and measure pass rate across several models.

## Known limits (honest)
- Free hosting: the container's database is rebuilt from the seed on restart, so applications made
  during testing are lost then. `/eval-dashboard` is served from a recorded run, not recomputed.
- Age proof is accepted as a file (type and size checked); nobody reads the document's content.
- The seed data has no age-proof files, so the officer sees `NO_AGE_PROOF_ON_FILE` on old cases.
