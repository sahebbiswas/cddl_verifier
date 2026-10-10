"""Single source of truth for the project version (Semantic Versioning 2.0.0).

Every pull request merged to ``main`` bumps this once (see CONTRIBUTING.md);
``pyproject.toml`` reads it dynamically and the CLI reports it via ``--version``.
"""

__version__ = "0.5.0"
