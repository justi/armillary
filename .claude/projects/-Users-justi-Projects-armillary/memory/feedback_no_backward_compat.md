---
name: No backward compatibility needed yet
description: User does not need backward compat guards for session state or internal refactors — armillary is pre-release alpha, no external users
type: feedback
---

Don't add backward compatibility shims for internal refactors (e.g. legacy dict-shaped session state, old config formats). armillary is pre-release alpha with one user — defensive guards for upgrade paths are premature and add code that will never fire.

**Why:** "na razie nie potrzebujemy zupełnie backward comp" — user feedback after codex review flagged a legacy session state guard.

**How to apply:** Skip backward compat guards unless user explicitly asks. Focus on clean code, not upgrade-path defense. If a refactor changes internal state shapes, just change them — no shims, no migration code.
