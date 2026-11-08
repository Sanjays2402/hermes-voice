#!/usr/bin/env bash
# Fail if the repository (current files AND every commit in its history) holds personal or secret data:
# home/drive paths, e-mail addresses, LAN addresses other than the documentation examples, keys/tokens,
# or commit authors that are not GitHub noreply addresses (GitHub's own noreply@github.com, used for merges made on github.com, is fine). Run before publishing; tools/build_release.sh runs it.
set -uo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)"
SELF=tools/check_clean.sh
fail=0

# 192.168.1.10 / .20 are the example addresses used in the docs; 172.17.0.x is Docker's default bridge.
PATTERNS=(
  'home or drive path|/Users/[A-Za-z0-9._-]+|/home/[a-z][a-z0-9._-]+/|/Volumes/[A-Za-z]|/private/(var|tmp)/|C:\\\\Users\\\\'
  'e-mail address|[A-Za-z0-9._%+-]+@(gmail|yahoo|outlook|hotmail|icloud|proton(mail)?)\.[a-z]+'
  'LAN address|192\.168\.[0-9]+\.([02-9]|1[1-9]|2[1-9]|[3-9][0-9]|1[0-9]{2}|2[0-5][0-9])\b|10\.[0-9]+\.[0-9]+\.[0-9]+\b|172\.(1[68-9]|2[0-9]|3[01])\.[0-9]+\.[0-9]+|172\.17\.([1-9]|[1-9][0-9]+)\.[0-9]+'
  'key or token|sk-[A-Za-z0-9_-]{16,}|eyJhbGci[A-Za-z0-9_-]{10,}|hv-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}|gh[ous]_[A-Za-z0-9]{20,}|xox[abp]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY'
)

revs=$(git rev-list --all)
for entry in "${PATTERNS[@]}"; do
  label=${entry%%|*}; re=${entry#*|}
  # shellcheck disable=SC2086
  hits=$(git grep -nIE "$re" $revs -- . ":!$SELF" 2>/dev/null | sed -E 's/^([0-9a-f]{7})[0-9a-f]*:/\1 /' | sort -u -t' ' -k2 | head -20)
  if [ -n "$hits" ]; then
    echo "FOUND $label (commit file:line):"; echo "$hits"; fail=1
  fi
done

bad_ids=$(git log --all --format='%ae%n%ce' | sort -u | grep -v -E '(@users\.noreply\.github\.com|^noreply@github\.com)$' || true)
if [ -n "$bad_ids" ]; then
  echo "FOUND commit author/committer e-mail that is not a GitHub noreply address:"; echo "$bad_ids"; fail=1
fi
msg_hits=$(git log --all --format='%h %B' | grep -nE '/Users/|/Volumes/|@gmail\.' || true)
if [ -n "$msg_hits" ]; then
  echo "FOUND personal data in commit messages:"; echo "$msg_hits"; fail=1
fi

if [ $fail -eq 0 ]; then echo "clean: no personal paths, addresses or secrets in files or history"; fi
exit $fail
