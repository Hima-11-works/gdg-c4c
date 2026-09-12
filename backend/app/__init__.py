from importlib.metadata import PackageNotFoundError, version

# Single source of truth for the version is pyproject.toml.
try:
    __version__ = version("pollution-backend")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"
