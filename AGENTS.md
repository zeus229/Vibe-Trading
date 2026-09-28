# AGENTS.md — Asistente Casa fork routing

This fork is operated as part of the **Asistente Casa** project. This file is intentionally small: it routes agents to the canonical project documentation instead of duplicating operational rules here.

## Before working on this fork

For any non-trivial change, upstream synchronization, validation, deployment, branch cleanup, or production investigation, read the canonical instructions in `zeus229/Asistente-Casa` first:

1. `AGENTS.md`
2. `docs/operations/CURRENT_STATE.md`
3. `docs/AI_CONTEXT.md`
4. `docs/ROADMAP.md`
5. `docs/operations/INDEX.md`
6. the relevant Vibe/investments operations document and ADRs
7. `docs/CHANGELOG_OPERATIVO.md` when investigating an incident

The Asistente Casa repository is the source of truth for our integration architecture, deployment/validation procedures, supported fork customizations, and operational documentation. Do **not** create a second operational wiki in this repository.

## Source-of-truth boundaries

- **This GitHub repository:** Vibe fork code, tests, and versioned Vibe-specific configuration.
- **Asistente Casa GitHub repository:** canonical cross-project architecture, integration and operational documentation.
- **Linux VM:** actual deployed state, Docker/runtime state, private configuration, databases, and secrets.

Never assume that GitHub is deployed, or that something working on the VM is versioned.

## Fork discipline

- Keep the fork as close to upstream as practical.
- Treat fork-only behavior as an explicit supported customization and keep it easy to remove or reapply.
- Do not recreate fixes already absorbed by upstream without first reconciling the current upstream/main state.
- Do not use branches as validation snapshots; use temporary detached worktrees for tests, smokes, audits, and A/B validation.
- Preserve branches associated with open PRs until they are merged, closed, or explicitly replaced and confirmed to contain no unique work.

## Local-agent role

Agents running on the VM primarily inspect and validate the real environment, run tests/E2E, operate Docker/services, and deploy versions selected by the project coordinator. By default they should not independently edit versioned code, commit, push, merge, or create replacement branches when the versioned change can be made through GitHub.

## Safety

`PPI_TRADING_DISABLED=True` is mandatory. Never enable real trading. Never expose or version secrets, tokens, private environment files, production databases, or runtime-private data.

Do not run `git reset --hard`, `git clean -fd`, `git push --force`, or `git push --force-with-lease` without explicit authorization.

When instructions here and the canonical Asistente Casa documentation differ, stop and reconcile the discrepancy before making a consequential change.
