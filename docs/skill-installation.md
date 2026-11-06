# Skill installation compatibility

The advertised Skills Hub install is currently blocked for this community skill. Track resolution in
[issue #4](https://github.com/majorminorlabs/hermes-voice/issues/4).
Use the [manual Home Assistant integration install](../README.md#manual-install) in the meantime.
The complete Echo flashing and recovery guide remains in both `devices/echo/README.md` and the skill's
`references/devices.md`. Setup scripts and required operations have not been removed or hidden.

## Reproduced findings — 2026-11-06

The complete skill at integration-fix commit `02263d9` was scanned without installing or executing it.
The original screenshot reported 27 findings; these are independent results, not a reproduction of
that exact version or count.

| Scanner | Findings | Verdict | Normal community installation |
|---|---|---|---|
| Installed `skills-guard-v8` | 1 critical, 4 high, 24 medium | dangerous | blocked |
| Upstream `skills-guard-v10` | 1 critical, 28 medium | dangerous | blocked |

The critical finding is `format_filesystem` on the manual ext4 formatting example in
`references/devices.md`. The four `sudo_usage` findings in that guide are high on v8 and informational
medium on v10. Remaining categories: 15 `python_subprocess`, 6 `hardcoded_ip_port`, one
`systemd_service`, one `macos_launchd`, and one `unicode_escape_chain`.

## Required operations and review

- `voice_profile.py` invokes Hermes to create/configure the dedicated profile and, on request, manage
  its gateway. Commands use argument arrays, without `shell=True`.
- `ha_setup.py` and `speech_setup.py` invoke Docker to inspect and manage Home Assistant and speech
  containers. Commands use argument arrays. Starting containers and changing HA configuration are
  real operations to review, not merely scanner noise.
- `screen_activity.py` invokes the kanban CLI and can install or remove a per-user launchd/systemd
  service. Its service creation is optional and has a dry-run. This is genuine persistence needed to
  keep the helper available after login.
- Loopback addresses describe local services. The Unicode escapes are an emoji/voice-cleanup character
  set in the bundled integration. Neither finding alone establishes malicious behavior.
- The formatting command is part of a human-operated device setup guide; installing the skill does
  not execute it. It remains visible for review rather than being excluded from the scan.

This is a review of the flagged operation types, not a claim of a complete security audit.
Removing only the formatting example would still leave the community package blocked by the caution
policy. Do not disguise subprocess calls, exclude runtime scripts from scanning, disable the scanner,
or change this repository to a trusted source locally.

Hermes needs a supported review/admission route for the complete, pinned community package, or a
maintainer-reviewed scanner/policy change. Approval must remain tied to the reviewed content and must
not automatically cover later updates. Existing upstream discussions include
[#141755](https://github.com/NousResearch/hermes-agent/issues/141755) (community-skill scan findings) and
[#94353](https://github.com/NousResearch/hermes-agent/issues/94353) (a separate agent-created write
approval deadlock; its approval commands are not a Skills Hub installation solution).

## Release validation

Run the actual Hermes scanner and unchanged install policy against the complete bundle before claiming
Skills Hub compatibility:

```sh
python3 tools/check_skill_install.py --hermes-repo /path/to/hermes-agent
```

This check performs a static scan only. It prints the scanner version, content hash, all finding
locations, and the normal installation decision. Exit status 1 means installation is blocked. It never
sets force or installs the skill. Test both the supported installed Hermes release and current upstream
source; preserve the scanner revision and bundle hash in release validation notes.

The issue stays open until the complete skill passes a supported installation path with scanning
enabled and without overriding findings. A successful unit-test run does not establish installation
compatibility.
