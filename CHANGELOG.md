# Changelog

Notable changes to `aimarket-agent`. Earlier releases: the git history.

## 2.6.2 — 2026-11-03

### Fixed
- **A2A: the answer to a paid task was checked against the wrong hub.** `pay_x402()` and `resume()` resolved the
  signing hub from the task that was waiting for payment, which carries no receipt artifact or capability metadata.
  They now resolve it from the completed answer, as `get_task()` already did.
