# Releasing quipu

A release is three things that must agree: the version string, the CHANGELOG section, and the tag.
CI builds the artifacts from the tag; everything below is what a person does before and after that.

## The version lives in two places

| file | what it holds | who reads it |
|---|---|---|
| `pyproject.toml` | `version = "0.3.0"` | the wheel and sdist filenames, and pip |
| `quipu/__init__.py` | `__version__ = "0.3.0"` | `quipu --version`, the MCP `serverInfo.version` |

Both must move together, in the same commit. A mismatch is not a build error: the wheel installs
and `quipu --version` prints a string the artifact does not carry, so the published version and
the reported version disagree with no signal that anything is wrong.

## CHANGELOG first

`## Unreleased` becomes the release heading, and the release heading goes at the top — newest
section first:

```markdown
## 0.3.0 — 2026-10-29
```

The heading must begin with `## 0.3.0`; the release workflow finds the section by that prefix and
uses it verbatim as the GitHub Release body. A released section is never edited afterwards — a fix
that lands later goes in the next version's section, because a release body that changes after
publication is a body nobody can trust.

## Build and verify locally before tagging

```bash
python -m build
python -m venv /tmp/relcheck && /tmp/relcheck/bin/pip install dist/quipu-0.3.0-py3-none-any.whl
/tmp/relcheck/bin/quipu --version
/tmp/relcheck/bin/quipu --vault /tmp/relcheck-vault init
/tmp/relcheck/bin/quipu mcp --help
```

`python -m build` needs the `build` package; it writes `dist/quipu-0.3.0.tar.gz` and
`dist/quipu-0.3.0-py3-none-any.whl`. The install is the point: a wheel that installs but whose
console script cannot import itself is the failure this catches, and only running the installed
binary finds it. `quipu --version` must print the version you just set, and `init` must make a
vault that the same binary can use.

## Tag, then let CI build

```console
git tag -a v0.3.0 -m "quipu 0.3.0"
git push origin v0.3.0
```

The `release.yml` workflow runs on the tag: it builds the sdist and the wheel, checks that the
wheel's version matches the tag, installs the wheel into a clean venv on Python 3.10, 3.12 and
3.13, and only then creates the GitHub Release with both artifacts attached and the CHANGELOG
section as its body. A failed matrix means no release exists yet, so fix and re-tag first.

## After the tag

Bump `main` to the next development version in `pyproject.toml` and `quipu/__init__.py` (the
`X.Y.ZaN` form this repo uses, e.g. `0.3.1a1`), so a later install from `main` cannot claim to
be the release — only the tagged artifact carries the number `0.3.0`.

```console
sed -i 's/^version = "0.3.0"/version = "0.3.1a1"/' pyproject.toml
sed -i 's/^__version__ = "0.3.0"/__version__ = "0.3.1a1"/' quipu/__init__.py
git commit -am "chore: 0.3.1a1 after the 0.3.0 tag"
git push origin main
```

## Rollback

Before anyone installs, a tag can be deleted and re-pointed — delete it locally and on the
remote, fix the commit, tag again. The release workflow does not run for a deleted tag, so no
artifact survives the mistake. After a wheel is in someone's hands, do not move `0.3.0`: cut
`0.3.1` with the fix and leave `0.3.0` as what it was.
