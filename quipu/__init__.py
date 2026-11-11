"""Deprecated alias for :mod:`sigil`.

The engine was called quipu until 2026-11-11; the name moved when the project did. `sigil` is the
package, the CLI and the vault now. This module exists for one release so that an existing
`import quipu`, an installed console script, or a config that still says `provider: quipu` keeps
working instead of failing at gateway start.

Submodules resolve too — `from quipu import config` and `import quipu.cli` return the *same module
objects* as their sigil counterparts, because a half-alias (top-level only) breaks exactly the callers
that were written against the old name most thoroughly. A meta-path finder does the mapping rather than
a directory of forwarding stubs, so a module added to sigil is reachable through the old name without
anyone remembering to add one here.
"""
import importlib
import importlib.abc
import importlib.util
import pathlib
import sys
import warnings

import sigil as sigil  # re-exported: `quipu.sigil` lets a caller reach the real package
from sigil import *  # noqa: F401,F403
from sigil import __version__  # noqa: F401


class _AliasFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """`quipu.<sub>` IS `sigil.<sub>` — one object, cached under both names."""

    prefix = "quipu."

    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith(self.prefix):
            return None
        # a submodule that exists in this shim's own directory (`__main__`, and any future one) is a
        # real file: let the path finder load it, because runpy and `inspect` need a loader with code
        local = pathlib.Path(__file__).parent / fullname[len(self.prefix):].replace(".", "/")
        if local.with_suffix(".py").exists() or (local / "__init__.py").exists():
            return None
        return importlib.util.spec_from_loader(fullname, self)

    def create_module(self, spec):
        real = importlib.import_module("sigil." + spec.name[len(self.prefix):])
        sys.modules[spec.name] = real  # `import quipu.config` and `sigil.config` must not diverge
        return real

    def exec_module(self, module):
        return None


if not any(isinstance(finder, _AliasFinder) for finder in sys.meta_path):
    sys.meta_path.insert(0, _AliasFinder())

warnings.warn(
    "quipu is now sigil: `import sigil`, `sigil` on the command line. This alias goes away next release.",
    DeprecationWarning,
    stacklevel=2,
)
