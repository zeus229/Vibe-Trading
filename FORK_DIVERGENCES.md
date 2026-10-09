# Fork divergences from HKUDS/Vibe-Trading

Last upstream sync: 2026-10-09 (upstream `b1f6ce71`, fork `211a72cc`).

## Grounding: derived-formula notes are fail-closed

`_formula_in_note` (`agent/src/agent/grounding/policies.py`) treats an ASCII colon as the
separator between descriptive labels and exactly one complete arithmetic expression.
Anything else after the colon (a stated result `= 1.25`, a trailing explanation,
units, a second formula) makes the note unextractable, so the derived figure is not
preserved and the model must repair it.

Upstream #1728 tolerates trailing results, explanations and units after the colon.
We keep the stricter rule on purpose: a derived figure is only accepted when the whole
suffix is machine arithmetic. The full-width colon keeps the tolerant upstream reading.

Tests: `tests/test_grounding_note_formula_extraction.py` (fork policy) and
`tests/test_grounding_formula_note.py` (upstream cases; the three tolerant ASCII-colon
cases are asserted to return `None`). If this policy is ever relaxed, change both.

## Argentina dashboard link

`frontend/src/components/layout/Layout.tsx` renders an "Argentina" link under
`SidebarNavigation` (`VITE_ASISTENTE_CASA_UI_URL`).
