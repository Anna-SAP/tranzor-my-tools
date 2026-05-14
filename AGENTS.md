# Agent Notes

Before making changes in this repository, read:

- `C:\Users\susu82\Tranzor-Platform\my-tools\.agent\context.md`

Key rules:

- `my-tools` is a standalone nested Git repository inside `Tranzor-Platform`.
- Default work scope is `my-tools` only.
- Treat the parent `Tranzor-Platform` repository as read-only unless the user explicitly says otherwise.
- Windows release builds must use `build_windows.ps1` or `TranzorExporter.spec`.
- Formal Mac app releases must use `.github/workflows/build-mac.yml`.
- Agent-opened PRs may be merged to `master` without asking — see "Autonomous PR handling" below for the exact conditions.

## Autonomous PR handling (agent-opened PRs)

The repository owner has pre-authorized agents (Claude Code or any similar
coding agent) to merge their **own** PRs to `master` without per-PR human
approval. The conditions and safety gates below are non-negotiable.

**Scope.** This rule applies *only* to PRs that the agent itself opened in
the current or a prior session. PRs opened by humans or third-party bots
still require explicit human go-ahead — do not merge them under this rule.

**CI gate.**

- If the PR has GitHub Actions check runs attached, wait for **all** of
  them to complete with a successful conclusion before merging. If any
  check fails, investigate the failure and push a fix instead of merging.
- If the PR has **no** check runs attached (typical for this repo, since
  `Build Mac App` and `Build Windows EXE` are `workflow_dispatch`-only and
  do not trigger on PR open), the agent merges based on its own review of
  the diff. The owner has explicitly accepted that the agent's diff
  judgment is the gate in the absence of CI.

**Hard stops** — even with the CI gate satisfied, the agent must pause
and ask the human if any of these are true:

- A human reviewer left a `REQUEST_CHANGES` review on the PR.
- The change is destructive or hard to reverse (deletions of user-facing
  features, history rewrites, secret rotation, large data migrations).
- The change modifies build / release workflows in a way that cannot be
  verified inside the agent's sandbox (e.g. CI changes that only manifest
  on a `macos-latest` runner) — prefer to ask the owner to manually
  trigger the workflow once before merging.
- The merge call itself errors (conflicts, branch protection rule,
  required-review violations). Do not try to bypass — report and ask.

**Self-approval.** GitHub does not allow the author of a PR to approve
their own PR. Under this rule, the agent does **not** call
`pull_request_review_write` with state `APPROVE` on its own PRs — the
merge call itself is the approval signal, backed by the durable
authorization recorded here.

**Merge procedure.**

1. Confirm scope (agent-opened) and CI gate.
2. Use `mcp__github__merge_pull_request`. Prefer `squash` for cleanliness
   unless the branch's commit history is already curated and meaningful.
3. After a successful merge, delete the head branch.
4. If anything goes wrong, stop and report — never force-push, never
   bypass branch protection.

This authorization was given in chat by the repository owner on
2026-05-14. Update or revoke this section by editing `AGENTS.md` itself
through a PR; do not rely on chat-only revocations.
