"""Cross-statement validation checks (Phase 5).

Deterministic Python checks that run after all extraction sub-agents finish.
Each check compares values across two workbooks to verify MFRS reconciliation
identities (e.g. SOFP total equity = SOCIE closing equity).
"""
