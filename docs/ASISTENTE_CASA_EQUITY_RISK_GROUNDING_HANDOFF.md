# Asistente Casa — Risk ACCIONES grounding: paused handoff (2026-10-13)

## Decision and current production

**The upstream update was deployed on 2026-10-13; keep the Risk ACCIONES conversational incident open until authenticated E2E verifies numeric delivery.** Do not merge [PR #71](https://github.com/zeus229/Vibe-Trading/pull/71)
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

## Update deployed 2026-10-13 — PASS with configuration reservation

The upstream sync PR [#72](https://github.com/zeus229/Vibe-Trading/pull/72) is merged;
fork `main@1d906e68d1c2fa89634df5d68e0de6845ee9ea2c` was deployed as
`vibe-trading:prod-1d906e68`. VM operator reports container
`6663c72b9b24558b54fc2927b9ba7e17679153f83e6a6ec66bc33bcf662bccc1`
healthy, zero restarts, `/live` and `/health` HTTP 200, and
`PPI_TRADING_DISABLED=True`. Rollback image `prod-6984e862` remains available.

Focused smoke: canonical ACCIONES risk (13/13, 100%, 252-window, ARS),
`portfolio_summary` compact and portfolio Risk X-Ray all `ok`; MCP scope
is available for authenticated conversation scope and denied for scheduled scope.
Grounding #1622 focused tests 267 PASS; mixed extraETF/native ARS refresh
ad-hoc PASS; 116 tools measured inside the image. No comprehensive E2E or
full test campaign was run. Unauthenticated `/sessions` returned 401, but a
**positive authenticated bearer request and complete conversational E2E were
not run**. Thus the original numeric-output incident is **not yet closed**.

**Operational reservation:** `/etc/asistente-casa-vibe.override.yml` still
references obsolete `prod-46cb5432`; the live deploy used
`/home/zeus229/vibe-override-1d906e68-20261013.yml` with Compose project
`vibe-trading`. Root access is needed to install the updated override. Before
future recreate/restart, use an explicit correct Compose override and verify
resolved image with `docker compose config` to avoid accidental rollback.
Run as an authorized sudo operator only after confirming the file is correct:
```sh
sudo cp /home/zeus229/vibe-override-1d906e68-20261013.yml /etc/asistente-casa-vibe.override.yml
```
Production checkout is clean at integrated SHA; historical uncommitted
`snapshot_id` hint is preserved separately in `stash@{0}` and
`/home/zeus229/vibe-prod-dirty-backup-20261013/` (not deployed).

Next: install persistent override, verify effective Compose configuration,
then run **one** authenticated conversational Risk ACCIONES scenario requiring
all 13 Euler contributions and the full 13×13 matrix in the final response.
If the response still drops valid figures, investigate remaining correction
semantics and PR #71 separately; do not assert that #1622 alone fixed it.

## Authenticated E2E after upstream #1622: FAIL (2026-10-13)

User-reported single production E2E: session `21303f2cdf3a`, attempt
`058b4f46655f`, image `prod-1d906e68`. Bearer authentication PASS;
canonical `consultar_riesgo_acciones_canonico(window=252)` invoked exactly
once, 13/13 assets, 100% coverage; no X-Ray substitution. Ledger captured
246 numeric leaves including 13 risk contributions and all 169 matrix cells,
but their evidence records lacked `symbol` despite indexed asset paths,
and `_pct` leaves were labeled `ratio`.

The 6,690-character initial draft contained both complete tables but no
`figures` block/exact refs. First grounding rejected 251 claims (247
`no_evidence`, four `value_mismatch`); correction emitted a 708-character
qualitative response with **0/13 contributions and 0/169 correlations**.
Final grounding accepted that shortened response. Model said it lacked the
exact call ID; that claim is an observation, not proof of what the correction
prompt actually contained (prompt absent from trace). Service stayed healthy,
trading remained disabled.

**Decision:** upstream #1622 alone did not resolve numeric delivery; retain
PR #71 unmerged. Valid figures were *not* previously approved by grounding,
so the clean-figure preservation mechanism does not apply. Inspect exact
call-ID candidate propagation, aggregate vs indexed per-symbol evidence,
percentage units, and the declaration/correction contract without weakening
fail-closed behavior. Reproduce with an isolated deterministic fixture first,
then one authenticated E2E after a reviewed candidate. No additional broad
test campaign. Private runtime evidence paths:
`/home/vibe/.vibe-trading/sessions/21303f2cdf3a/trace.jsonl`,
`/home/vibe/.vibe-trading/runs/20261013_140250_57_d2b483/artifacts/grounding_evidence.json`.


## Generic grounding fix — isolated replay PASS, not yet integrated (2026-10-14 handoff)

Codex validated a new **generic** approach in detached worktree against
`main@6f92aeba36988825f96e8a6705d1234fcc582ef1`. The old risk-only renderer
is **not** recommended. New changes address four files:
`agent/src/agent/grounding/evidence.py`,
`agent/src/agent/grounding/figures.py`,
`agent/src/agent/grounding/policies.py`, and newly added
`agent/tests/test_grounding_mcp_keyed_metrics.py`.

Findings: keyed map leaves did not inherit metric semantics; risk contributions
did not carry the per-ticker identity; financial semantic recognition excluded
the `mcp_` transport prefix; correlations need verified row **and** column
context rather than claiming each pair is about a single stock. Correction
now supplies exact `tool_call_id::field` candidates but still requires the
model to cite evidence and pass full grounding validation (no auto-approval).

Codex synthetic fixture replay PASS: all 13 contributions, 169 cells, volatility
and 183 required figures preserved; prose without refs rejected; qualitative
correction rejected; altered numeric leaves and missing matrix cell rejected.
**93 focused tests, compilation, imports and diff PASS.** This does NOT
constitute real authenticated E2E or a production deployment.

Validated patch remains VM-local:
`/tmp/vibe-risk-grounding-generic.patch` SHA-256
`7a047c0a57473fa93f4984ed7f258ee55caa63e58495404835dd2082fa907df6`.
**Not accessible from GitHub or ChatGPT filesystem and not integrated**.
A report describing its behavior cannot be safely substituted for its complete
source without an independent implementation and validation. Preserve a
persistent copy outside `/tmp`; never version secrets or actual session data.

Current PR [#71](https://github.com/zeus229/Vibe-Trading/pull/71) head
`4c2563e1400207a641b489cdf3164f0d4b565fb2`, 134 commits behind
main when inspected; do not overwrite upstream with an old-base file update.
Update this candidate **non-destructively** against current main or replace it
only with explicit continuity and preservation of unique work. ChatGPT owns
GitHub commits; Codex validates. Gate: review the actual code, rerun focused
fixtures on updated main, then execute one authenticated E2E proving complete
numeric user-visible output and no broadened scheduled/no-owner permissions.
Risk arithmetic remains Asistente Casa-only and trading stays disabled.
