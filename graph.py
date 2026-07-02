"""graph.py — code-graph edge extraction and traversal for code-rag.

Provides three public functions:
  extract_file_edges  — AST-based edge extraction from a single file
  find_refs           — look up definitions and references for a symbol
  bfs_expand          — BFS traversal of the code graph from a file or symbol
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Optional

from chunking import _detect_language, _extract_symbols


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _node_text(node) -> str:
    return node.text.decode("utf-8", errors="ignore")


def _resolve_module(module_name: str, base_dir: Path, repo_root: Path) -> tuple[str, str]:
    """Resolve a dotted module name to (dst_kind, dst).

    Searches base_dir / <a/b>.py then base_dir / <a/b> / __init__.py.
    Falls back to ('external', module_name). Resolutions landing outside
    repo_root (deep relative imports walking above the repo) are treated as
    external so out-of-repo paths never enter the edges table.
    """
    parts = module_name.split(".")
    if not module_name or not all(parts):
        return "external", module_name
    rel = Path(*parts)
    for candidate in (base_dir / rel.with_suffix(".py"), base_dir / rel / "__init__.py"):
        if candidate.exists():
            try:
                candidate.resolve().relative_to(repo_root.resolve())
            except (ValueError, OSError):
                return "external", module_name
            return "file", str(candidate)
    return "external", module_name


def _walk_tree(node):
    """Yield every node in the AST via depth-first traversal."""
    yield node
    for child in node.children:
        yield from _walk_tree(child)


# ---------------------------------------------------------------------------
# extract_file_edges
# ---------------------------------------------------------------------------

def extract_file_edges(
    path: Path,
    repo_name: str,
    repo_root: Path,
) -> list[tuple]:
    """Extract graph edges from a single source file.

    Returns a list of 8-tuples:
        (repo, src_kind, src, dst_kind, dst, edge_type, path, lineno)

    Supported edge types:
      defines:    file defines a symbol  (all supported languages)
      imports:    file imports a module  (Python only)
      references: file references a symbol via call or inheritance  (Python only)
    """
    language = _detect_language(path)
    if language is None:
        return []

    try:
        source = path.read_text(errors="ignore")
    except Exception as e:
        print(f"[graph] extract failed for {path}: {e}", file=sys.stderr)
        return []

    src_str = str(path)
    edges: list[tuple] = []

    # --- defines (all supported languages via chunking._extract_symbols) ---
    try:
        symbols = _extract_symbols(source, language)
        for name, start, _end in symbols:
            edges.append((
                repo_name, "file", src_str,
                "symbol", name,
                "defines",
                src_str, start,
            ))
    except Exception as e:
        print(f"[graph] extract failed for {path}: {e}", file=sys.stderr)
        return []

    if language != "python":
        return edges

    # --- Python: imports and references via tree-sitter AST ---
    try:
        import tree_sitter_languages as tsl

        parser = tsl.get_parser(language)
        tree = parser.parse(source.encode("utf-8", errors="ignore"))
        root = tree.root_node

        # --- Imports ---
        for node in _walk_tree(root):
            if node.type == "import_statement":
                # import a.b  /  import a.b as c
                lineno = node.start_point[0] + 1
                for child in node.children:
                    # grammar versions emit single-name imports as either
                    # dotted_name or bare identifier
                    if child.type in ("dotted_name", "identifier"):
                        module_name = _node_text(child)
                        dst_kind, dst = _resolve_module(module_name, repo_root, repo_root)
                        edges.append((
                            repo_name, "file", src_str,
                            dst_kind, dst,
                            "imports",
                            src_str, lineno,
                        ))
                    elif child.type == "aliased_import":
                        name_node = child.child_by_field_name("name")
                        if name_node and name_node.type == "dotted_name":
                            module_name = _node_text(name_node)
                            dst_kind, dst = _resolve_module(module_name, repo_root, repo_root)
                            edges.append((
                                repo_name, "file", src_str,
                                dst_kind, dst,
                                "imports",
                                src_str, lineno,
                            ))

            elif node.type == "import_from_statement":
                lineno = node.start_point[0] + 1
                _handle_from_import(
                    node, lineno,
                    repo_name, src_str, path, repo_root, edges,
                )

        # --- References: function calls and class inheritance ---
        seen_refs: set[tuple[str, int]] = set()

        for node in _walk_tree(root):
            if node.type == "call":
                func_node = node.child_by_field_name("function")
                if func_node is None:
                    continue
                if func_node.type == "identifier":
                    name = _node_text(func_node)
                elif func_node.type == "attribute":
                    attr_node = func_node.child_by_field_name("attribute")
                    if attr_node is None:
                        continue
                    name = _node_text(attr_node)
                else:
                    continue
                lineno = node.start_point[0] + 1
                key = (name, lineno)
                if key not in seen_refs:
                    seen_refs.add(key)
                    edges.append((
                        repo_name, "file", src_str,
                        "symbol", name,
                        "references",
                        src_str, lineno,
                    ))

            elif node.type == "class_definition":
                # Superclasses in argument_list (tree-sitter field: superclasses)
                superclasses_node = node.child_by_field_name("superclasses")
                if superclasses_node is None:
                    continue
                class_lineno = node.start_point[0] + 1
                for sc in superclasses_node.children:
                    if sc.type == "identifier":
                        name = _node_text(sc)
                        key = (name, class_lineno)
                        if key not in seen_refs:
                            seen_refs.add(key)
                            edges.append((
                                repo_name, "file", src_str,
                                "symbol", name,
                                "references",
                                src_str, class_lineno,
                            ))
                    elif sc.type == "attribute":
                        attr_node = sc.child_by_field_name("attribute")
                        if attr_node is not None:
                            name = _node_text(attr_node)
                            key = (name, class_lineno)
                            if key not in seen_refs:
                                seen_refs.add(key)
                                edges.append((
                                    repo_name, "file", src_str,
                                    "symbol", name,
                                    "references",
                                    src_str, class_lineno,
                                ))

    except Exception as e:
        print(f"[graph] extract failed for {path}: {e}", file=sys.stderr)
        # Keep defines edges; discard partially-collected imports/references
        edges = [e for e in edges if e[5] == "defines"]

    return edges


def _handle_from_import(
    node,
    lineno: int,
    repo_name: str,
    src_str: str,
    path: Path,
    repo_root: Path,
    edges: list,
) -> None:
    """Parse a single import_from_statement and append edges."""
    # Detect relative import: look for a 'relative_import' child or a
    # 'module_name' child whose text starts with '.'
    is_relative = False
    relative_level = 0
    relative_module: Optional[str] = None

    # tree-sitter-python grammar:
    #   from .core import X   → children: from, relative_import(.core), import, X
    #   from . import X       → children: from, relative_import(.), import, X
    #   from pkg.core import X → children: from, dotted_name(pkg.core), import, X
    # The 'module_name' field may point to relative_import or dotted_name.

    module_name_node = node.child_by_field_name("module_name")

    if module_name_node is not None and module_name_node.type == "relative_import":
        is_relative = True
        text = _node_text(module_name_node)
        relative_level = len(text) - len(text.lstrip("."))
        # Check for a module name inside the relative_import
        for rc in module_name_node.children:
            if rc.type == "dotted_name":
                relative_module = _node_text(rc)
                break

    if not is_relative:
        # Also check direct children for relative_import node
        for child in node.children:
            if child.type == "relative_import":
                is_relative = True
                text = _node_text(child)
                relative_level = len(text) - len(text.lstrip("."))
                for rc in child.children:
                    if rc.type == "dotted_name":
                        relative_module = _node_text(rc)
                        break
                break

    if is_relative:
        # Compute base directory: level 1 = path.parent, level 2 = grandparent, …
        # Stop at filesystem root so an absurd dot count cannot walk past it.
        base_dir = path.parent
        for _ in range(relative_level - 1):
            if base_dir == base_dir.parent:
                break
            base_dir = base_dir.parent

        if relative_module:
            # from .core import X  →  resolve "core" relative to base_dir
            dst_kind, dst = _resolve_module(relative_module, base_dir, repo_root)
            edges.append((
                repo_name, "file", src_str,
                dst_kind, dst,
                "imports",
                src_str, lineno,
            ))
        else:
            # from . import X, Y  →  each imported name resolved from base_dir
            import_started = False
            for child in node.children:
                if child.type in ("import", "import_keyword") or child.text == b"import":
                    import_started = True
                    continue
                if not import_started:
                    continue
                if child.type in ("dotted_name", "identifier"):
                    name = _node_text(child)
                    dst_kind, dst = _resolve_module(name, base_dir, repo_root)
                    edges.append((
                        repo_name, "file", src_str,
                        dst_kind, dst,
                        "imports",
                        src_str, lineno,
                    ))
                elif child.type == "aliased_import":
                    name_node = child.child_by_field_name("name")
                    if name_node:
                        name = _node_text(name_node)
                        dst_kind, dst = _resolve_module(name, base_dir, repo_root)
                        edges.append((
                            repo_name, "file", src_str,
                            dst_kind, dst,
                            "imports",
                            src_str, lineno,
                        ))
    else:
        # Absolute from-import: from a.b import c
        if module_name_node is not None:
            module_name = _node_text(module_name_node)
            dst_kind, dst = _resolve_module(module_name, repo_root, repo_root)
            edges.append((
                repo_name, "file", src_str,
                dst_kind, dst,
                "imports",
                src_str, lineno,
            ))


# ---------------------------------------------------------------------------
# find_refs
# ---------------------------------------------------------------------------

def find_refs(
    conn: sqlite3.Connection,
    repo: str,
    symbol: str,
    limit: int = 50,
) -> list[dict]:
    """Return definitions and references for a symbol.

    Definitions precede references; each group is sorted by (path, lineno).
    The limit is consumed by definitions first; remaining slots go to references.
    """
    defs = conn.execute(
        "SELECT path, lineno FROM edges "
        "WHERE repo=? AND edge_type='defines' AND dst=? "
        "ORDER BY path, lineno LIMIT ?",
        (repo, symbol, limit),
    ).fetchall()

    result: list[dict] = [
        {"path": row["path"], "lineno": row["lineno"], "kind": "definition"}
        for row in defs
    ]

    remaining = limit - len(result)
    if remaining > 0:
        refs = conn.execute(
            "SELECT path, lineno FROM edges "
            "WHERE repo=? AND edge_type='references' AND dst=? "
            "ORDER BY path, lineno LIMIT ?",
            (repo, symbol, remaining),
        ).fetchall()
        result.extend(
            {"path": row["path"], "lineno": row["lineno"], "kind": "reference"}
            for row in refs
        )

    return result


# ---------------------------------------------------------------------------
# bfs_expand
# ---------------------------------------------------------------------------

def bfs_expand(
    conn: sqlite3.Connection,
    repo: str,
    target: str,
    depth: int = 1,
    max_results: int = 50,
) -> dict:
    """BFS traversal of the code graph starting from a file or symbol node.

    Returns a dict:
        target      {'kind': 'file'|'symbol'|None, 'value': target}
        depth       clamped depth used
        related     list of node dicts (kind, value, edge_type, depth, via, lineno)
        truncated   True if max_results was reached
        error       'target_not_found' | None
    """
    depth = max(1, min(3, depth))
    max_results = max(1, min(50, max_results))

    # --- Resolve target kind ---
    target_kind: Optional[str] = None

    row = conn.execute(
        "SELECT 1 FROM edges WHERE repo=? AND (src=? OR path=?) LIMIT 1",
        (repo, target, target),
    ).fetchone()
    if row is not None:
        target_kind = "file"

    if target_kind is None:
        row = conn.execute(
            "SELECT 1 FROM edges WHERE repo=? AND edge_type='defines' AND dst=? LIMIT 1",
            (repo, target),
        ).fetchone()
        if row is not None:
            target_kind = "symbol"

    if target_kind is None:
        return {
            "target": {"kind": None, "value": target},
            "depth": depth,
            "related": [],
            "truncated": False,
            "error": "target_not_found",
        }

    # --- BFS ---
    related: list[dict] = []
    truncated = False

    # visited tracks (kind, value) pairs; start includes the seed node
    visited: set[tuple[str, str]] = {(target_kind, target)}
    frontier: list[tuple[str, str]] = [(target_kind, target)]

    _reverse_map = {
        "imports": "imported_by",
        "defines": "defined_in",
        "references": "referenced_by",
    }

    for hop in range(1, depth + 1):
        if not frontier:
            break

        frontier_values = [v for (_k, v) in frontier]
        placeholders = ",".join("?" * len(frontier_values))

        # Forward edges: src IN frontier
        fwd_rows = conn.execute(
            f"SELECT src_kind, src, dst_kind, dst, edge_type, lineno "
            f"FROM edges WHERE repo=? AND src IN ({placeholders})",
            [repo] + frontier_values,
        ).fetchall()

        # Reverse edges: dst IN frontier
        rev_rows = conn.execute(
            f"SELECT src_kind, src, dst_kind, dst, edge_type, lineno "
            f"FROM edges WHERE repo=? AND dst IN ({placeholders})",
            [repo] + frontier_values,
        ).fetchall()

        next_frontier: list[tuple[str, str]] = []

        def _add_node(node_kind: str, node_value: str, edge_type: str, via: str, lineno) -> bool:
            """Append to related; return True if max_results reached."""
            key = (node_kind, node_value)
            if key in visited:
                return False
            visited.add(key)
            related.append({
                "kind": node_kind,
                "value": node_value,
                "edge_type": edge_type,
                "depth": hop,
                "via": via,
                "lineno": lineno,
            })
            if node_kind != "external":
                next_frontier.append(key)
            return len(related) >= max_results

        # Structural relations first (imports < defines < references) so that
        # truncation at max_results keeps the file-level structure instead of
        # being swamped by a single file's long defines/references list.
        _priority = {
            "imports": 0, "imported_by": 0,
            "defines": 1, "defined_in": 1,
            "references": 2, "referenced_by": 2,
        }
        candidates: list[tuple[int, str, str, str, str, object]] = []
        for row in fwd_rows:
            et = row["edge_type"]
            candidates.append((
                _priority.get(et, 9),
                row["dst_kind"], row["dst"], et, row["src"], row["lineno"],
            ))
        for row in rev_rows:
            et = _reverse_map.get(row["edge_type"], row["edge_type"])
            candidates.append((
                _priority.get(et, 9),
                row["src_kind"], row["src"], et, row["dst"], row["lineno"],
            ))
        candidates.sort(key=lambda c: c[0])

        done = False
        for _prio, node_kind, node_value, et, via, ln in candidates:
            if _add_node(node_kind, node_value, et, via, ln):
                done = True
                break

        if done:
            truncated = True
            break

        frontier = next_frontier

    return {
        "target": {"kind": target_kind, "value": target},
        "depth": depth,
        "related": related,
        "truncated": truncated,
        "error": None,
    }
