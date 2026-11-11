"""`python -m quipu` still runs the CLI, because a module path is the other thing people hardcode."""
import sys

from sigil.cli import main

sys.exit(main())
