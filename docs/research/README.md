# Research log

Hermes Voice is built in the open and on real hardware. Each log here records one line of work the way a study
would: the question, what was checked and how, the evidence, what was decided and why, what was built, how it
was tested and what happened, including the dead ends. When someone asks "why does it work this way?" or "did
anyone try X?", the answer should be in one of these files.

Conventions:

- Dates are absolute (YYYY-MM-DD). Times are local to the test setup.
- **Evidence** is something checked, with the command, file or observation, not a belief. Versions are
  given where they matter (Home Assistant, Hermes Agent, echo-dot-assist, Fire OS).
- **Decision** entries say what was chosen, the alternatives, and why.
- Personal details (addresses, names, keys, local paths) are left out; `<echo-ip>`, `<home-assistant>` and
  the like stand in for them.
- A log is appended to, not rewritten: a later finding that overturns an earlier one says so.

| Log | Topic |
|---|---|
| [2026-11-05-echo-show-5.md](2026-11-05-echo-show-5.md) | Installing an Echo Show 5 1st gen and adding it to Hermes Voice |
| [2026-11-05-hermes-screen.md](2026-11-05-hermes-screen.md) | A screen for the agent on the Echo Show: feed, page, app, background work |
| [2026-11-06-wake-word-permissive.md](2026-11-06-wake-word-permissive.md) | Retraining "Hey Hermes" on permissively licensed data, and a test on audio it never heard |
