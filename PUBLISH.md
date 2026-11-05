# Publishing (manual)

Nothing here has been run. Replace `OWNER` with your GitHub user or organization.

## 1. Fill in the owner

```bash
cd hermes-voice   # the repository root
grep -rl "github.com/OWNER/hermes-voice" . --exclude-dir=.git
# edit those files (manifest.json documentation/issue_tracker, wakeword/pipeline.py manifest website),
# and add your GitHub handle to "codeowners" in custom_components/hermes_voice/manifest.json, e.g. ["@you"]
git commit -am "Set repository owner"
```

## 2. Check before pushing

Commits must use a GitHub noreply identity, not your personal e-mail (it becomes public). This repo is set
up with `hermes-voice contributors <hermes-voice@users.noreply.github.com>`; to use your own:
`git config user.email <id>+<user>@users.noreply.github.com` (GitHub → Settings → Emails).

```bash
tests/run.sh                                   # tests + ruff + mypy must pass
git status --ignored                           # secrets/, ha-config/, .env, wakeword/work/ must be ignored
tools/check_clean.sh                           # no personal paths, e-mails, LAN IPs or secrets in files or history
```

## 3. Create the GitHub repository and push

```bash
gh repo create OWNER/hermes-voice --public --description "Talk to a Hermes Agent profile through Home Assistant Assist" --source . --remote origin
git push -u origin main
```

(Without `gh`: create an empty public repository named `hermes-voice` on github.com, then
`git remote add origin git@github.com:OWNER/hermes-voice.git && git push -u origin main`.)

## 4. Tag v0.1.0 and make a release

```bash
tools/build_release.sh       # dist/: integration zip, skill zip, wake word zip, SHA256SUMS (from HEAD)
git tag -a v0.1.0 -m "Hermes Voice 0.1.0"
git push origin v0.1.0
gh release create v0.1.0 --title "Hermes Voice 0.1.0" --notes-file CHANGES.md dist/*
```

After setting the owner, check the skill installs from GitHub on a spare profile:
`hermes -p <spare> skills install OWNER/hermes-voice/skill/hermes-voice-setup`.

HACS uses GitHub releases/tags as versions. Keep `custom_components/hermes_voice/manifest.json` `version`
in step with the tag.

## 5. Add to HACS as a custom repository (each user)

Home Assistant → HACS → ⋮ (top right) → **Custom repositories** → URL
`https://github.com/OWNER/hermes-voice`, type **Integration** → Add → find **Hermes Voice** → Download →
restart Home Assistant → Settings → Devices & services → Add integration → Hermes Voice.

## 6. Optional: HACS default list

Submitting to the HACS default repositories requires the repo to pass HACS validation (GitHub Action
`hacs/action`) and Home Assistant's `hassfest`; add both as CI workflows first and follow
https://hacs.xyz/docs/publish/include.

## 7. Optional: share the Hermes skill

The skill lives in `skill/hermes-voice-setup/` and carries copies of the integration and the wake word
model (`assets/`, refreshed by `tools/sync_skill_assets.sh`; `tests/run.sh` fails if they are stale).
Users install it with `hermes skills install OWNER/hermes-voice/skill/hermes-voice-setup`, or it can be
published with `hermes skills publish` (see Hermes docs, *Creating Skills → Publishing Skills*).
