#!/usr/bin/env python3
"""Check the complete setup skill with a selected Hermes scanner and normal install policy."""

import argparse
import importlib.util
from pathlib import Path
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes-repo", type=Path, required=True)
    args = parser.parse_args()
    scanner = args.hermes_repo.resolve() / "tools" / "skills_guard.py"
    if not scanner.is_file():
        parser.error("--hermes-repo must contain tools/skills_guard.py")
    spec = importlib.util.spec_from_file_location("hermes_release_skills_guard", scanner)
    if spec is None or spec.loader is None:
        parser.error("cannot load Hermes skills scanner")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    skill = Path(__file__).resolve().parents[1] / "skill" / "hermes-voice-setup"
    result = module.scan_skill(skill, source="majorminorlabs/hermes-voice")
    allowed, reason = module.should_allow_install(result, force=False)
    revision = subprocess.run(
        ["git", "-C", str(args.hermes_repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    print(f"Scanner: {module.SCANNER_VERSION}")
    print(f"Hermes revision: {revision.stdout.strip() or 'unavailable'}")
    print(f"Bundle: {module.content_hash(skill)}")
    print(f"Verdict: {result.verdict}")
    for finding in result.findings:
        print(f"{finding.severity}: {finding.pattern_id} {finding.file}:{finding.line}")
    print(reason)
    return 0 if allowed is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
