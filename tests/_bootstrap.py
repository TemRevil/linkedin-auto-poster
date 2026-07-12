"""Shared test bootstrap: put src/ on sys.path so tests can import the modules.

The project keeps its modules under src/ with no installed package, so each
test module does `from _bootstrap import SRC  # noqa` (or imports this first)
to make `import humanizer`, `import database`, etc. resolve. Run the suite with:

    python -m unittest discover -s tests -v
"""
import os
import sys

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC not in sys.path:
    sys.path.insert(0, SRC)
