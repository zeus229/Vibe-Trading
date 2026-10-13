# Asistente Casa — Risk ACCIONES grounding: paused handoff (2026-10-13)

## Decision and current production

**Pause all changes and deployments for this issue until the planned upstream
update on 2026-10-14.** Do not merge [PR #71](https://github.com/zeus229/Vibe-Trading/pull/71)
until it has been compared against the newer upstream grounding/correction code,
validated on the resulting base, and passed an authenticated E2E.

- **Asistente Casa production PASS:** `main@fc2e24cf9ecd0f65b09c41a51a859107f8fefa40`;
  canonical MCP `consultar_riesgo_acciones_canonico` successfully provides
  63/126/252/504/756 windows. In a 252-return real dispatch, 13/13 ACCIONES,
  100% family weight, 13 Euler risk contributions, 169 correlation cells,
  annualized volatility 35.35%, and 2025-09-30–2026-10-08 return period were present.
- **Vibe production is operational, E2E delivery FAIL:** `vibe-trading:prod-6984e862`,
  commit `6984e8629cf615dc0bb45d754482a06c866ff407`, healthy.
  Authenticated conversational routing called the canonical MCP exactly once;
  no silent Risk X-Ray substitution. Scheduled Research and ownerless sessions
  still cannot call it. The final visible answer dropped valid numerical results.
- **Root cause from Codex replay:** tool payload of 8,486 characters was untruncated
  under `data`. Initial draft contained numbers without required `figures`
  declarations (197 rejections). Correction dropped the tables and used a tool
  *name* as evidence rather than the exact `tool_call_id`. The validator
  suggested an unambiguous exact reference but correction did not apply it.
  13 risk contribution leaves and 169 matrix leaves existed throughout the
  observed tool ledger. No financial-data loss.
- **PR #71:** branch `fix/canonical-equity-risk-grounded-output` remains DRAFT;
  initial candidate `4c2563e1400207a641b489cdf3164f0d4b565fb2`
  only improves prompt policy. It is **not the validated deterministic fix**.
  Check actual remote head before further work.

## Isolated validated patch, NOT published or merged

Codex tested a replay-based deterministic solution that generates risk tables
and grounded declarations from observed MCP payload, preserves mandatory figures
through correction retries, and applies unique exact-reference repairs only
when values and paths match. It rejects ambiguity, absent or altered leaves,
duplicate JSON keys, incomplete/asymmetric matrices, or conflicting tool calls.
**1,857 tests PASS** including adverse replay; 13 contributions and 169 correlations
survived to final validated text (187 declarations incl. scope/coverage figures).
An authenticated E2E of **this patch** was NOT executed.

Changes in five files:

1. `agent/src/agent/grounding/canonical_equity_risk.py` (new).
2. `agent/src/agent/grounding/ledger.py`.
3. `agent/src/agent/grounding/release.py`.
4. `agent/src/agent/loop.py`.
5. `agent/tests/test_canonical_equity_risk_rendering.py` (new).

Artifact in Codex VM: `/tmp/vibe-pr71-canonical-equity-grounding.patch`
(40,161 bytes, SHA-256 `31aaa1240450b65d0e896721aeb8dcabae861a76d23fe3141768248f5d948d8a`).
It was **not** uploaded to GitHub; Codex has neither GitHub CLI credentials nor
an authenticated write connector. A URL to the patch does not exist.
`/tmp` is ephemeral; preserve a persistent copy **outside any tracked repo**,
verify the same hash, and do not copy secrets or real call IDs. ChatGPT handles
future versioned changes directly in GitHub.

## Design boundaries and resume criteria

**Generic solutions first; keep upstream modifications small.**
Asistente Casa owns canonical positions, OHLC, event adjustments, deterministic
risk calculations, structured fields and data quality. Vibe consumes and
explains these results, and owns its conversation-specific tool-call identity,
grounding, correction and presentation. Prefer removing duplicated data
logic from Vibe; do not make Asistente Casa generate or guess Vibe call IDs.
Avoid hardcoding tickers, risk values, incident-specific call IDs or relaxing
the general grounding guard. Treat any generic fixes discovered in the
upstream upgrade as replacements for our local patch when equivalent.

1. Preserve the artifact and SHA without GitHub credentials on VM.
2. Review updated upstream grounding changes and PRs in detached worktree; sync
   fork under the upstream/high-risk workflow, separately from PR #71.
3. Reproduce the original adversarial replay on that new baseline **before**
   applying private patches.
4. Apply only still-needed minimal generic changes to the existing PR candidate,
   by ChatGPT in GitHub. Codex validates tests and VM; no local commits/push.
5. E2E authenticated conversation must **actually call the canonical MCP** and
   deliver 13/13 contribution percentages, complete 13×13 correlation matrix,
   volatility, timestamps, window and valid `tool_call_id::data.<numeric leaf>`
   figures in its final user-visible answer. Verify scheduled and ownerless
   access remains denied, SQLite read-only, no historical PPI/Yahoo downloads,
   and `PPI_TRADING_DISABLED=True`.

**Production success is not claimed.** Both deployed services remain healthy,
and no rollback was necessary, but the final Vibe conversational output is
not validated. This is intentionally parked so unrelated work can continue.
