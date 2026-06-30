"""mfs-server package.

``__version__`` is resolved from the installed distribution metadata (built from
``pyproject.toml``), so it always tracks the real package version instead of a
hard-coded string. Versions are lockstep across mfs-cli / mfs-server / SDKs.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mfs-server")
except PackageNotFoundError:  # running from a source tree with no installed metadata
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
