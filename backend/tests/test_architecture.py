"""Enforces the layer boundaries described in docs/architecture.md."""

import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"

# layer -> layers it may import. The root package (app/__init__.py, for
# __version__) is importable from anywhere; app/main.py is the composition
# root and may import everything.
ALLOWED_IMPORTS: dict[str, set[str]] = {
    "core": set(),
    "domain": {"core"},
    # models depends on domain only for the AlertSeverity enum used as a
    # column type (app.models.tables); it has no other domain knowledge.
    "models": {"core", "domain"},
    "db": {"core", "domain", "models"},
    "ingestion": {"core", "domain"},
    "services": {"core", "domain", "ingestion", "models", "db"},
    # api may import services for business logic (routes stay thin) and db
    # only for the dependency-injection wiring in app/api/deps.py.
    "api": {"core", "domain", "db", "services"},
}


def imported_layers(path: Path) -> set[str]:
    layers = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        else:
            continue
        for name in names:
            parts = name.split(".")
            if parts[0] == "app" and len(parts) > 1:
                layers.add(parts[1])
    return layers


def test_every_layer_has_a_rule() -> None:
    layers = {p.name for p in APP_DIR.iterdir() if p.is_dir() and p.name != "__pycache__"}

    assert layers == set(ALLOWED_IMPORTS)


def test_layers_only_import_allowed_layers() -> None:
    violations = []
    for layer, allowed in ALLOWED_IMPORTS.items():
        for path in (APP_DIR / layer).rglob("*.py"):
            for imported in imported_layers(path) - allowed - {layer}:
                violations.append(f"{path.relative_to(APP_DIR)} imports app.{imported}")

    assert violations == []
