<!--
Title: Conventional Commits, under 70 chars. e.g. feat(store): add ref allocation
-->

Closes #

## Summary

<!-- What changed and why. Name the tradeoff if there was one. -->

## Test plan

<!--
Required for anything touching runtime behaviour. Not "CI passes" — numbered,
copy-pasteable steps with concrete expected outcomes. Tick each box and paste
the actual output once it passes; `gh pr edit` the body rather than leaving it
aspirational.

Pure-docs or pure-CI changes: a one-line "no runtime change; verification is CI
alone" is fine, but say it explicitly.
-->

- [ ] **1.**
  ```bash
  ```
  Expected:

- [ ] **2. CI green**

**Pass criteria:**

## Sensitivity / stream-safety check

<!--
bench-recon is the system of record for sensitive engagement data (client and
target identity, scope, authorization, findings, credentials). benchhud only
ever gets the ref-safe projection. Delete this section only if the change
genuinely cannot touch any of that.
-->

- [ ] No sensitive field added to anything the benchhud projection / `/api/engagements` returns
- [ ] The projection leak test still passes (it scans the whole serialized output)
- [ ] No sensitive data in logs, error messages, or fixtures
- [ ] No real client/target data in tests
