# CI is staged, not pushed (token lacks the workflow scope) — see docs/ci.md
# CI is staged, not yet pushed

`.github/workflows/ci.yml` exists on disk but has never been pushed: the deploy token
(`GITHUB_TOKEN` in `~/.hermes/.env`) carries only `repo, delete_repo`, and GitHub refuses any push
that creates or edits a file under `.github/workflows/` without the `workflow` scope.

The file is therefore excluded locally via `.git/info/exclude` (not `.gitignore`, so it lands the
moment the scope exists):

```bash
# after adding the workflow scope to the PAT
git add -f .github/workflows/ci.yml
git commit -m "ci: add the test/lint/smoke workflow"
git push
# then make it required on main:
#   Settings → Branches → main → Require status checks → test (3.10, 3.12, 3.13)
```

Until then the gate runs locally and is quoted in every PR: `make test && make lint`.
