"""duckgrep: a DuckDB index of a codebase for coding agents."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("duckgrep")  # from the installed package's metadata, so it can't drift from pyproject
except PackageNotFoundError:  # running from a source tree that isn't installed
    __version__ = "0+unknown"
