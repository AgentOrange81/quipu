"""The rename is an alias, not a cutover: both names must keep working until the old one is retired.

Every assertion here is a surface another process depends on — an import, an installed console script,
the Hermes memory-provider entry point, the MCP identity, and the `--version` banner. A rename that
breaks one of them breaks a profile's gateway at startup, which is why they are pinned rather than
documented.
"""
from __future__ import annotations

import importlib.metadata as md
import re
from pathlib import Path

import pytest

import sigil

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_the_old_name_imports_the_same_engine():
    """A config or a script that still says `import quipu` must reach the identical objects."""
    import quipu

    assert quipu.__version__ == sigil.__version__
    assert quipu.register is sigil.register


def test_the_alias_says_so_when_it_is_used():
    """A silent alias becomes permanent by accident."""
    import importlib
    import sys
    import warnings

    sys.modules.pop("quipu", None)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.import_module("quipu")

    messages = [str(w.message) for w in caught if issubclass(w.category, DeprecationWarning)]
    assert any("sigil" in m for m in messages), messages


def test_both_console_scripts_point_at_one_main():
    assert re.search(r'^sigil = "sigil\.cli:main"$', PYPROJECT, re.M)
    assert re.search(r'^sigil-capture = "sigil\.capture:main"$', PYPROJECT, re.M)
    assert re.search(r'^quipu = "sigil\.cli:main"$', PYPROJECT, re.M)
    assert re.search(r'^quipu-capture = "sigil\.capture:main"$', PYPROJECT, re.M)


def test_the_memory_provider_entry_point_exists_under_both_names():
    """Hermes resolves `provider: <name>` from the installed metadata; dropping the old key would make
    an existing profile fail at gateway start rather than fall back."""
    assert re.search(r'^sigil = "sigil:register"$', PYPROJECT, re.M)
    assert re.search(r'^quipu = "sigil:register"$', PYPROJECT, re.M)


def test_the_installed_entry_points_agree_when_the_package_is_installed():
    try:
        points = md.entry_points(group="hermes_agent.memory_providers")
    except Exception:  # pragma: no cover - an uninstalled source tree
        pytest.skip("no installed metadata")
    names = {p.name for p in points if p.value.startswith("sigil")}
    if not names:
        pytest.skip("package not installed in this interpreter")
    assert {"sigil", "quipu"} <= names


def test_the_version_banner_says_sigil(capsys):
    from sigil import cli

    with pytest.raises(SystemExit):
        cli.main(["--version"])

    assert capsys.readouterr().out.strip() == f"sigil {sigil.__version__}"


def test_the_mcp_server_calls_itself_sigil():
    """An agent reading `serverInfo.name` should see the product it is talking to."""
    from sigil import mcp

    source = mcp.__file__ and Path(mcp.__file__).read_text(encoding="utf-8")

    assert '"serverInfo": {"name": "sigil"' in source
    assert '"serverInfo": {"name": "quipu"' not in source


def test_the_old_module_path_still_runs_the_cli():
    """`python -m quipu` is the other thing people hardcode, and runpy needs a real loader for it —
    a meta-path-only shim answers `import quipu` and fails `-m quipu` with `AttributeError: get_code`."""
    import subprocess
    import sys

    out = subprocess.run([sys.executable, "-m", "quipu", "--version"],
                         capture_output=True, text=True, cwd=ROOT)

    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == f"sigil {sigil.__version__}"
