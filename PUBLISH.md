# Publishing

The intended GitHub repository is `majorminorlabs/hermes-voice`.

## 1. Resolve the wake word model's distribution rights

The `hey_hermes.tflite` model was trained with mixed-license data. The upstream
[negative features dataset](https://huggingface.co/datasets/kahrendt/microwakeword) is CC BY-NC 4.0,
and the training notebook describes its result as suitable for non-commercial personal use.
Before pushing publicly, confirm that the model can be redistributed or exclude both copies of
the weights from the published history and release files. See [Credits](CREDITS.md).

## 2. Check before pushing

Use your GitHub noreply address for commit attribution. Copy it from GitHub → Settings → Emails.
The credentials used to push to the repository are separate from the author identity on each commit.
To credit a second GitHub account on a commit, add a trailer after a blank line:

```text
Co-authored-by: NAME <ID+USERNAME@users.noreply.github.com>
```

See [GitHub's co-author instructions](https://docs.github.com/en/pull-requests/how-tos/commit-changes/creating-a-commit-with-multiple-authors).

```bash
tests/run.sh                                   # tests + ruff + mypy must pass
git status --ignored                           # secrets/, ha-config/, .env, wakeword/work/ must be ignored
tools/check_clean.sh                           # no personal paths, e-mails, LAN IPs or secrets in files or history
```

## 3. Create the GitHub repository and push

Review the commits and merge the approved release changes into local `main` before pushing.

```bash
gh repo create majorminorlabs/hermes-voice --public --description "Talk to a Hermes Agent profile through Home Assistant Assist" --source . --remote origin
git push -u origin main
```

(Without `gh`: create an empty public repository named `hermes-voice` on github.com, then
`git remote add origin git@github.com:majorminorlabs/hermes-voice.git && git push -u origin main`.)

## 4. Tag v0.1.0 and make a release

Run from `main` after it has been pushed.

```bash
tools/build_release.sh       # dist/: integration zip, skill zip, wake word zip, SHA256SUMS (from HEAD)
git tag -a v0.1.0 -m "Hermes Voice 0.1.0"
git push origin v0.1.0
gh release create v0.1.0 --title "Hermes Voice 0.1.0" --notes-file CHANGES.md dist/*
```

After the push, check the skill installs from GitHub on a spare profile:
`hermes -p <spare> skills install majorminorlabs/hermes-voice/skill/hermes-voice-setup`.

HACS uses GitHub releases/tags as versions. Keep `custom_components/hermes_voice/manifest.json` `version`
in step with the tag.

## 5. Add to HACS as a custom repository (each user)

Home Assistant → HACS → ⋮ (top right) → **Custom repositories** → URL
`https://github.com/majorminorlabs/hermes-voice`, type **Integration** → Add → find **Hermes Voice** → Download →
restart Home Assistant → Settings → Devices & services → Add integration → Hermes Voice.

## 6. Optional: HACS default list

Submitting to the HACS default repositories requires the repo to pass HACS validation (GitHub Action
`hacs/action`) and Home Assistant's `hassfest`; add both as CI workflows first and follow
https://hacs.xyz/docs/publish/include.

## 7. Optional: share the Hermes skill

The skill lives in `skill/hermes-voice-setup/` and carries copies of the integration and the wake word
model (`assets/`, refreshed by `tools/sync_skill_assets.sh`; `tests/run.sh` fails if they are stale).
Users install it with `hermes skills install majorminorlabs/hermes-voice/skill/hermes-voice-setup`, or it can be
published with `hermes skills publish` (see Hermes docs, *Creating Skills → Publishing Skills*).
