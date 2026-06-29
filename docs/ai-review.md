# AI code review on every PR

Every pull request gets an automated, architecture-level review by Claude. It
posts **inline comments** on the changed lines and emits a structured verdict.
A gate then **blocks the merge** when there is any *critical* finding; warnings
are informational and never block.

- Workflow: [`.github/workflows/ai-review.yml`](../.github/workflows/ai-review.yml)
- Verdict schema: [`.github/review-schema.json`](../.github/review-schema.json)

## What it looks for (and what it ignores)

Focus — "what breaks production", at the level of architecture and behaviour:

- **Business-logic correctness** — wrong conditions, broken control flow, bad state transitions.
- **SQL injection / injection vectors** — SQL built with f-strings instead of the
  parameterised `query()`/`execute()` helpers; unsanitised input reaching SQL,
  the shell, HTML, or email headers (CR/LF in SMTP).
- **Unhandled edge cases** — `None`/empty/missing inputs, unchecked external
  responses (AI API, job sources, SMTP), error paths, races.
- **N+1 queries** and performance regressions.

Always escalated to **critical** when a change touches **auth/sessions**,
**payments/account activation**, or **data deletion** — e.g. a missing CSRF token
(`_csrf` + `csrf_token()`), a missing `_owned_app()` ownership check, an absent
admin/privilege check, or an irreversible delete without confirmation.

**Ignored on purpose:** style, formatting, naming, import order, line length, and
subjective preferences. The reviewer is told to skip these entirely.

## Severity → merge behaviour

| Severity   | Inline comment | Blocks merge? |
|------------|----------------|---------------|
| `critical` | yes            | **yes** — gate job fails |
| `warning`  | yes            | no — informational only |

The gate fails *closed*: if the reviewer can't produce a verdict (e.g. a bad/missing
token), the check fails so a human notices rather than silently letting a PR through.

## One-time setup

### 1. Create the Claude OAuth token

This uses your existing Claude Max/Pro subscription — **no per-token API billing.**

```bash
claude setup-token
```

Copy the token it prints.

### 2. Add it as a repository secret

GitHub → repo **Settings → Secrets and variables → Actions → New repository secret**:

- Name: `CLAUDE_CODE_OAUTH_TOKEN`
- Value: the token from step 1

Or with the GitHub CLI:

```bash
gh secret set CLAUDE_CODE_OAUTH_TOKEN --repo TD-Bethel/AutoApplyBW
```

### 3. Require the check before merging

The status check only appears in the list *after the workflow has run once*, so
open a throwaway PR (or push to this branch's PR) first, then:

**UI:** Settings → Branches → Add branch ruleset / protect `main` →
*Require status checks to pass before merging* → search and select **AI Code Review**.

**CLI:**

```bash
gh api -X PUT repos/TD-Bethel/AutoApplyBW/branches/main/protection \
  -H "Accept: application/vnd.github+json" \
  -f 'required_status_checks[strict]=true' \
  -f 'required_status_checks[contexts][]=AI Code Review' \
  -f 'enforce_admins=false' \
  -f 'required_pull_request_reviews=' \
  -f 'restrictions='
```

With `enforce_admins=false`, repo admins can still override a stuck gate in an
emergency; set it to `true` to make the gate absolute.

## Tuning

- **Model:** the workflow defaults to `claude-sonnet-4-6` (fast, light on
  subscription limits). For the deepest review, change the `--model` line in
  `claude_args` to `claude-opus-4-8`.
- **Triggers:** it runs on PR `opened`, `synchronize`, `reopened`, and
  `ready_for_review`, and skips drafts. Adjust the `on:`/`if:` blocks to taste.
- **Prompt:** the review instructions live inline in the workflow's `prompt:`
  field — edit there to add or relax focus areas.
