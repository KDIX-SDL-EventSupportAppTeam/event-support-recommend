"""モジュール境界 (docs/rules/coding.md, docs/specs/08-architecture.md §1)。

features/ と drsa/ は分析リポジトリが import する（あるいは手計算でテストする）ので、
FastAPI・SQL・HTTP・相互依存を持ち込ませない。
"""

from __future__ import annotations

import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "event_support_recommend"

FORBIDDEN = {
    "features": ("fastapi", "starlette", "pydantic", "sqlalchemy", "httpx",
                 "event_support_recommend.drsa", "event_support_recommend.strategies",
                 "event_support_recommend.api", "event_support_recommend.data"),
    "drsa": ("fastapi", "starlette", "pydantic", "sqlalchemy", "httpx",
             "event_support_recommend.features", "event_support_recommend.strategies",
             "event_support_recommend.api", "event_support_recommend.data"),
    "strategies": ("fastapi", "starlette", "sqlalchemy", "httpx",
                   "event_support_recommend.api", "event_support_recommend.data"),
}


def _imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level:  # 相対 import を絶対名へ寄せる
                mod = f"event_support_recommend.{mod}" if mod else "event_support_recommend"
            names.add(mod)
    return names


def test_layer_imports_are_one_directional():
    violations: list[str] = []
    for layer, forbidden in FORBIDDEN.items():
        for py in (SRC / layer).rglob("*.py"):
            for imp in _imports(py):
                if any(imp == f or imp.startswith(f + ".") for f in forbidden):
                    violations.append(f"{layer}/{py.name} -> {imp}")
    assert not violations, violations
