"""tests/test_graph.py — code-graph extraction, queries, BFS, watcher sync.

Covers graph.extract_file_edges / find_refs / bfs_expand and the watcher's
path-scoped edges resync (FR-006).
"""
from __future__ import annotations

import sqlite3
import textwrap
from pathlib import Path

import pytest

import graph


# ---------------------------------------------------------------------------
# Helpers (mirrored from test_indexer_sqlite.py)
# ---------------------------------------------------------------------------

def _make_config(tmp_path: Path) -> dict:
    """Build a minimal config dict with an absolute DB path."""
    db_path = tmp_path / "code_rag.db"
    return {
        "storage": {"path": str(db_path)},
        "embedding": {
            "provider": "fastembed",
            "model": "BAAI/bge-small-en-v1.5",
            "dimension": 384,
            "batch_size": 8,
        },
        "chunking": {
            "chunk_lines": 40,
            "chunk_lines_overlap": 5,
            "max_chars": 2000,
        },
        "hygiene": {
            "respect_gitignore": False,
            "max_file_bytes": 102400,
            "max_file_lines": 5000,
            "secret_scan": False,
        },
    }


def _make_mini_repo(repo_path: Path) -> dict[str, Path]:
    """Create the standard pkg/ mini-repo fixture.

    Layout
    ------
    pkg/__init__.py   — empty
    pkg/core.py       — def helper(): ... / class Base: ...
    pkg/app.py        — from pkg.core import helper / import os /
                        class App(Base): ... calls helper() /
                        comment "# helper is great" and string "call helper here"
    pkg/dup.py        — def helper(): ... (duplicate symbol name)

    Returns a dict mapping logical name → absolute Path.
    """
    repo_path.mkdir(parents=True, exist_ok=True)
    pkg = repo_path / "pkg"
    pkg.mkdir()

    init = pkg / "__init__.py"
    init.write_text("", encoding="utf-8")

    core = pkg / "core.py"
    core.write_text(
        textwrap.dedent("""\
            def helper():
                return "core helper"


            class Base:
                pass
        """),
        encoding="utf-8",
    )

    app = pkg / "app.py"
    app.write_text(
        textwrap.dedent("""\
            from pkg.core import helper
            import os

            # helper is great
            _msg = "call helper here"


            class App(Base):
                def run(self):
                    helper()
        """),
        encoding="utf-8",
    )

    dup = pkg / "dup.py"
    dup.write_text(
        textwrap.dedent("""\
            def helper():
                return "dup helper"
        """),
        encoding="utf-8",
    )

    return {"init": init, "core": core, "app": app, "dup": dup}


def _insert_edges(conn: sqlite3.Connection, edges: list[tuple]) -> None:
    """Bulk-insert edge tuples into the edges table.

    Tuple order: (repo, src_kind, src, dst_kind, dst, edge_type, path, lineno)
    """
    conn.executemany(
        "INSERT INTO edges (repo, src_kind, src, dst_kind, dst, edge_type, path, lineno) "
        "VALUES (?,?,?,?,?,?,?,?)",
        edges,
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Test 1: extract — imports resolution (absolute)
# ---------------------------------------------------------------------------

class TestExtractImports:
    def test_absolute_import_resolves_to_file(self, tmp_path):
        """from pkg.core import helper → dst_kind='file', dst=abs path of core.py"""
        files = _make_mini_repo(tmp_path / "repo")
        repo_root = tmp_path / "repo"
        app = files["app"]
        core = files["core"]

        edges = graph.extract_file_edges(app, "myrepo", repo_root)
        imports = [e for e in edges if e[6] == str(app) and e[5] == "imports"]

        # core.py import must resolve to a file
        core_edges = [e for e in imports if e[4] == str(core)]
        assert len(core_edges) >= 1, (
            f"Expected imports edge to core.py, got imports: {imports}"
        )
        assert core_edges[0][3] == "file", "dst_kind should be 'file' for local module"

    def test_stdlib_import_is_external(self, tmp_path):
        """import os → dst_kind='external', dst='os'"""
        files = _make_mini_repo(tmp_path / "repo")
        repo_root = tmp_path / "repo"
        app = files["app"]

        edges = graph.extract_file_edges(app, "myrepo", repo_root)
        imports = [e for e in edges if e[5] == "imports"]

        os_edges = [e for e in imports if e[4] == "os"]
        assert len(os_edges) >= 1, "Expected external imports edge for 'os'"
        assert os_edges[0][3] == "external", "dst_kind should be 'external' for stdlib"

    def test_tuple_columns_order(self, tmp_path):
        """Each edge tuple has 8 elements in DB column order."""
        files = _make_mini_repo(tmp_path / "repo")
        repo_root = tmp_path / "repo"
        edges = graph.extract_file_edges(files["core"], "myrepo", repo_root)
        for e in edges:
            assert len(e) == 8, f"Edge tuple should have 8 elements, got {len(e)}: {e}"

    def test_repo_field_matches_argument(self, tmp_path):
        """All edges carry the repo name passed as argument."""
        files = _make_mini_repo(tmp_path / "repo")
        repo_root = tmp_path / "repo"
        edges = graph.extract_file_edges(files["app"], "myrepo", repo_root)
        for e in edges:
            assert e[0] == "myrepo", f"repo column mismatch: {e[0]}"


# ---------------------------------------------------------------------------
# Test 2: extract — relative imports
# ---------------------------------------------------------------------------

class TestExtractRelativeImports:
    def test_relative_import_resolves_to_sibling_file(self, tmp_path):
        """from .core import helper → resolves to pkg/core.py"""
        repo_root = tmp_path / "repo"
        pkg = repo_root / "pkg"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        core = pkg / "core.py"
        core.write_text("def helper(): pass\n", encoding="utf-8")

        rel_module = pkg / "rel_app.py"
        rel_module.write_text(
            textwrap.dedent("""\
                from .core import helper
            """),
            encoding="utf-8",
        )

        edges = graph.extract_file_edges(rel_module, "myrepo", repo_root)
        imports = [e for e in edges if e[5] == "imports"]

        resolved = [e for e in imports if e[4] == str(core)]
        assert len(resolved) >= 1, (
            f"Relative import should resolve to {core}, imports={imports}"
        )
        assert resolved[0][3] == "file"

    def test_relative_import_from_dot_resolves(self, tmp_path):
        """from . import core → resolves to pkg/core.py"""
        repo_root = tmp_path / "repo"
        pkg = repo_root / "pkg"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        core = pkg / "core.py"
        core.write_text("def helper(): pass\n", encoding="utf-8")

        rel_module = pkg / "rel_app2.py"
        rel_module.write_text(
            textwrap.dedent("""\
                from . import core
            """),
            encoding="utf-8",
        )

        edges = graph.extract_file_edges(rel_module, "myrepo", repo_root)
        imports = [e for e in edges if e[5] == "imports"]

        # core can resolve to pkg/core.py or pkg/core/__init__.py
        core_file_edges = [
            e for e in imports
            if Path(e[4]).stem == "core" or e[4] == str(core)
        ]
        assert len(core_file_edges) >= 1, (
            f"'from . import core' should resolve, imports={imports}"
        )


# ---------------------------------------------------------------------------
# Test 3: extract — defines
# ---------------------------------------------------------------------------

class TestExtractDefines:
    def test_defines_function(self, tmp_path):
        """pkg/core.py exports 'helper' as a defines edge."""
        files = _make_mini_repo(tmp_path / "repo")
        repo_root = tmp_path / "repo"
        edges = graph.extract_file_edges(files["core"], "myrepo", repo_root)

        defines = [e for e in edges if e[5] == "defines"]
        names = [e[4] for e in defines]  # dst = symbol name
        assert "helper" in names, f"Expected 'helper' in defines, got {names}"

    def test_defines_class(self, tmp_path):
        """pkg/core.py exports 'Base' class as a defines edge."""
        files = _make_mini_repo(tmp_path / "repo")
        repo_root = tmp_path / "repo"
        edges = graph.extract_file_edges(files["core"], "myrepo", repo_root)

        defines = [e for e in edges if e[5] == "defines"]
        names = [e[4] for e in defines]
        assert "Base" in names, f"Expected 'Base' in defines, got {names}"

    def test_defines_lineno_is_correct(self, tmp_path):
        """defines lineno should match the actual definition line."""
        files = _make_mini_repo(tmp_path / "repo")
        repo_root = tmp_path / "repo"
        edges = graph.extract_file_edges(files["core"], "myrepo", repo_root)

        defines = [e for e in edges if e[5] == "defines"]
        helper_def = [e for e in defines if e[4] == "helper"]
        assert helper_def, "helper defines edge should exist"
        lineno = helper_def[0][7]
        assert lineno == 1, f"helper defined on line 1 in core.py, got {lineno}"

        base_def = [e for e in defines if e[4] == "Base"]
        assert base_def, "Base defines edge should exist"
        base_lineno = base_def[0][7]
        assert base_lineno == 5, f"Base defined on line 5 in core.py, got {base_lineno}"

    def test_defines_src_kind_is_file(self, tmp_path):
        """src_kind for defines edges must be 'file'."""
        files = _make_mini_repo(tmp_path / "repo")
        repo_root = tmp_path / "repo"
        edges = graph.extract_file_edges(files["core"], "myrepo", repo_root)

        for e in edges:
            if e[5] == "defines":
                assert e[1] == "file", f"src_kind should be 'file', got {e[1]}"
                assert e[3] == "symbol", f"dst_kind should be 'symbol', got {e[3]}"


# ---------------------------------------------------------------------------
# Test 4: extract — references are AST-only (no comments / strings)
# ---------------------------------------------------------------------------

class TestExtractReferences:
    def test_references_includes_function_call(self, tmp_path):
        """helper() call in App.run → references edge for 'helper'."""
        files = _make_mini_repo(tmp_path / "repo")
        repo_root = tmp_path / "repo"
        edges = graph.extract_file_edges(files["app"], "myrepo", repo_root)

        refs = [e for e in edges if e[5] == "references"]
        names = [e[4] for e in refs]
        assert "helper" in names, f"Expected 'helper' in references, got {names}"

    def test_references_includes_base_class(self, tmp_path):
        """class App(Base) → references edge for 'Base'."""
        files = _make_mini_repo(tmp_path / "repo")
        repo_root = tmp_path / "repo"
        edges = graph.extract_file_edges(files["app"], "myrepo", repo_root)

        refs = [e for e in edges if e[5] == "references"]
        names = [e[4] for e in refs]
        assert "Base" in names, f"Expected 'Base' in references, got {names}"

    def test_references_excludes_comment_word(self, tmp_path):
        """'great' appears only in a comment → must NOT appear in references."""
        files = _make_mini_repo(tmp_path / "repo")
        repo_root = tmp_path / "repo"
        edges = graph.extract_file_edges(files["app"], "myrepo", repo_root)

        refs = [e for e in edges if e[5] == "references"]
        names = [e[4] for e in refs]
        assert "great" not in names, (
            "Comment-only word 'great' should not appear in references"
        )

    def test_references_helper_lineno_not_comment_or_string_line(self, tmp_path):
        """helper references lineno must NOT point to comment (line 4) or string (line 5).

        app.py layout (1-indexed):
          1: from pkg.core import helper
          2: import os
          3: (blank)
          4: # helper is great
          5: _msg = "call helper here"
          6: (blank)
          7: (blank)
          8: class App(Base):
          9:     def run(self):
         10:         helper()
        """
        files = _make_mini_repo(tmp_path / "repo")
        repo_root = tmp_path / "repo"
        edges = graph.extract_file_edges(files["app"], "myrepo", repo_root)

        comment_line = 4   # "# helper is great"
        string_line = 5    # '_msg = "call helper here"'

        helper_ref_linenos = [
            e[7] for e in edges if e[5] == "references" and e[4] == "helper"
        ]
        assert helper_ref_linenos, "There should be at least one references edge for 'helper'"
        assert comment_line not in helper_ref_linenos, (
            f"references lineno should not include comment line {comment_line}"
        )
        assert string_line not in helper_ref_linenos, (
            f"references lineno should not include string line {string_line}"
        )

    def test_references_excludes_string_literal_word(self, tmp_path):
        """'call' appears only in a string literal → must NOT appear in references."""
        files = _make_mini_repo(tmp_path / "repo")
        repo_root = tmp_path / "repo"
        edges = graph.extract_file_edges(files["app"], "myrepo", repo_root)

        refs = [e for e in edges if e[5] == "references"]
        names = [e[4] for e in refs]
        assert "call" not in names, (
            "String-literal word 'call' should not appear in references"
        )


# ---------------------------------------------------------------------------
# Test 5: extract — unsupported extension returns empty list
# ---------------------------------------------------------------------------

class TestExtractUnsupportedExtension:
    def test_txt_file_returns_empty(self, tmp_path):
        """.txt file → []"""
        txt = tmp_path / "notes.txt"
        txt.write_text("some content\n", encoding="utf-8")

        result = graph.extract_file_edges(txt, "myrepo", tmp_path)
        assert result == [], f"Expected [] for .txt file, got {result}"

    def test_no_exception_on_unsupported(self, tmp_path):
        """extract_file_edges must not raise for unsupported extensions."""
        other = tmp_path / "data.csv"
        other.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
        try:
            result = graph.extract_file_edges(other, "myrepo", tmp_path)
        except Exception as exc:
            pytest.fail(f"extract_file_edges raised unexpectedly: {exc}")
        assert result == []


# ---------------------------------------------------------------------------
# Test 6 & 7: find_refs — ordering and duplicate definitions
# ---------------------------------------------------------------------------

class TestFindRefs:
    def _build_db(self, tmp_path: Path) -> sqlite3.Connection:
        """Build a DB and insert synthetic edges for the mini-repo."""
        import indexer

        config = _make_config(tmp_path)
        files = _make_mini_repo(tmp_path / "repo")
        repo_root = tmp_path / "repo"
        conn = indexer.get_db(config)

        # Build edges from each file so find_refs has real data.
        for key in ("core", "app", "dup"):
            edges = graph.extract_file_edges(files[key], "myrepo", repo_root)
            _insert_edges(conn, edges)

        return conn, files

    def test_find_refs_definitions_come_first(self, tmp_path):
        """definition rows precede reference rows in find_refs result."""
        conn, _ = self._build_db(tmp_path)
        results = graph.find_refs(conn, "myrepo", "helper")
        conn.close()

        assert results, "find_refs should return at least one result for 'helper'"
        kinds = [r["kind"] for r in results]
        # All definitions must appear before any reference
        saw_reference = False
        for k in kinds:
            if k == "reference":
                saw_reference = True
            if saw_reference and k == "definition":
                pytest.fail("definition appeared after reference in find_refs result")

    def test_find_refs_result_is_list_of_dicts(self, tmp_path):
        """Each item in find_refs result has 'path', 'lineno', 'kind' keys."""
        conn, _ = self._build_db(tmp_path)
        results = graph.find_refs(conn, "myrepo", "helper")
        conn.close()

        for item in results:
            assert "path" in item, f"Missing 'path' key: {item}"
            assert "lineno" in item, f"Missing 'lineno' key: {item}"
            assert "kind" in item, f"Missing 'kind' key: {item}"
            assert item["kind"] in ("definition", "reference"), (
                f"Unexpected kind value: {item['kind']}"
            )

    def test_find_refs_multiple_definitions_all_returned(self, tmp_path):
        """helper is defined in both core.py and dup.py; both definitions returned."""
        conn, files = self._build_db(tmp_path)
        results = graph.find_refs(conn, "myrepo", "helper")
        conn.close()

        defs = [r for r in results if r["kind"] == "definition"]
        def_paths = {r["path"] for r in defs}
        assert str(files["core"]) in def_paths, (
            f"core.py definition missing from find_refs. def_paths={def_paths}"
        )
        assert str(files["dup"]) in def_paths, (
            f"dup.py definition missing from find_refs. def_paths={def_paths}"
        )

    def test_find_refs_definitions_sorted_by_lineno(self, tmp_path):
        """definitions group is sorted by lineno ascending."""
        conn, _ = self._build_db(tmp_path)
        results = graph.find_refs(conn, "myrepo", "helper")
        conn.close()

        defs = [r for r in results if r["kind"] == "definition"]
        linenos = [r["lineno"] for r in defs]
        assert linenos == sorted(linenos), (
            f"Definitions should be lineno-ascending: {linenos}"
        )

    def test_find_refs_references_sorted_by_lineno(self, tmp_path):
        """references group is sorted by lineno ascending."""
        conn, _ = self._build_db(tmp_path)
        results = graph.find_refs(conn, "myrepo", "helper")
        conn.close()

        refs = [r for r in results if r["kind"] == "reference"]
        linenos = [r["lineno"] for r in refs]
        assert linenos == sorted(linenos), (
            f"References should be lineno-ascending: {linenos}"
        )


# ---------------------------------------------------------------------------
# Test 8: find_refs — unknown symbol returns []
# ---------------------------------------------------------------------------

class TestFindRefsUnknown:
    def test_unknown_symbol_returns_empty(self, tmp_path):
        import indexer

        config = _make_config(tmp_path)
        conn = indexer.get_db(config)
        result = graph.find_refs(conn, "myrepo", "totally_nonexistent_xyz")
        conn.close()
        assert result == [], f"Expected [], got {result}"


# ---------------------------------------------------------------------------
# Tests 9-14: bfs_expand
# ---------------------------------------------------------------------------

class TestBfsExpand:
    def _build_db_with_edges(self, tmp_path: Path):
        """Set up DB with mini-repo edges pre-inserted."""
        import indexer

        config = _make_config(tmp_path)
        files = _make_mini_repo(tmp_path / "repo")
        repo_root = tmp_path / "repo"
        conn = indexer.get_db(config)

        for key in ("core", "app", "dup"):
            edges = graph.extract_file_edges(files[key], "myrepo", repo_root)
            _insert_edges(conn, edges)

        return conn, files, repo_root

    # Test 9: depth=1 file target
    def test_bfs_depth1_file_target_outbound(self, tmp_path):
        """app.py at depth=1: must include imports edge to core.py."""
        conn, files, _ = self._build_db_with_edges(tmp_path)
        result = graph.bfs_expand(conn, "myrepo", str(files["app"]), depth=1)
        conn.close()

        assert result["error"] is None, f"Unexpected error: {result['error']}"
        related = result["related"]
        values = [r["value"] for r in related]
        assert str(files["core"]) in values, (
            f"core.py should appear in bfs_expand from app.py depth=1. "
            f"related values: {values}"
        )

    def test_bfs_depth1_file_target_inbound(self, tmp_path):
        """core.py at depth=1: must include imported_by edge from app.py."""
        conn, files, _ = self._build_db_with_edges(tmp_path)
        result = graph.bfs_expand(conn, "myrepo", str(files["core"]), depth=1)
        conn.close()

        assert result["error"] is None
        related = result["related"]
        imported_by = [r for r in related if r["edge_type"] == "imported_by"]
        paths = [r["value"] for r in imported_by]
        assert str(files["app"]) in paths, (
            f"app.py should appear as imported_by for core.py. paths={paths}"
        )

    def test_bfs_depth1_defines_in_result(self, tmp_path):
        """app.py at depth=1: defines edges (App symbol) should appear."""
        conn, files, _ = self._build_db_with_edges(tmp_path)
        result = graph.bfs_expand(conn, "myrepo", str(files["app"]), depth=1)
        conn.close()

        related = result["related"]
        define_entries = [r for r in related if r["edge_type"] == "defines"]
        assert define_entries, (
            f"Expected defines edges from app.py at depth=1. related={related}"
        )

    # Test 10: depth clamping
    def test_bfs_depth_clamped_100_becomes_3(self, tmp_path):
        """depth=100 → returned dict depth field == 3."""
        conn, files, _ = self._build_db_with_edges(tmp_path)
        result = graph.bfs_expand(conn, "myrepo", str(files["app"]), depth=100)
        conn.close()
        assert result["depth"] == 3, f"Expected depth=3, got {result['depth']}"

    def test_bfs_depth_clamped_0_becomes_1(self, tmp_path):
        """depth=0 → returned dict depth field == 1."""
        conn, files, _ = self._build_db_with_edges(tmp_path)
        result = graph.bfs_expand(conn, "myrepo", str(files["app"]), depth=0)
        conn.close()
        assert result["depth"] == 1, f"Expected depth=1, got {result['depth']}"

    def test_bfs_depth_clamped_negative_becomes_1(self, tmp_path):
        """depth=-5 → returned dict depth field == 1."""
        conn, files, _ = self._build_db_with_edges(tmp_path)
        result = graph.bfs_expand(conn, "myrepo", str(files["app"]), depth=-5)
        conn.close()
        assert result["depth"] == 1

    def test_bfs_max_results_clamped_to_50(self, tmp_path):
        """max_results=999 → treated as 50 (no crash, no more than 50)."""
        conn, files, _ = self._build_db_with_edges(tmp_path)
        result = graph.bfs_expand(
            conn, "myrepo", str(files["app"]), depth=3, max_results=999
        )
        conn.close()
        assert len(result["related"]) <= 50

    def test_bfs_max_results_clamped_to_1(self, tmp_path):
        """max_results=0 → treated as 1."""
        conn, files, _ = self._build_db_with_edges(tmp_path)
        result = graph.bfs_expand(
            conn, "myrepo", str(files["app"]), depth=1, max_results=0
        )
        conn.close()
        assert len(result["related"]) <= 1

    # Test 11: circular import safety
    def test_bfs_circular_import_no_infinite_loop(self, tmp_path):
        """Mutual imports at depth=3 should terminate without duplicates."""
        import indexer

        config = _make_config(tmp_path)
        repo_root = tmp_path / "repo"
        repo_root.mkdir(parents=True)

        file_a = repo_root / "a.py"
        file_b = repo_root / "b.py"
        file_a.write_text("import b\n", encoding="utf-8")
        file_b.write_text("import a\n", encoding="utf-8")

        conn = indexer.get_db(config)
        for fp in (file_a, file_b):
            edges = graph.extract_file_edges(fp, "myrepo", repo_root)
            _insert_edges(conn, edges)

        result = graph.bfs_expand(conn, "myrepo", str(file_a), depth=3)
        conn.close()

        assert result["error"] is None
        values = [r["value"] for r in result["related"]]
        # No duplicate values in related
        assert len(values) == len(set(values)), (
            f"Duplicate nodes in circular BFS result: {values}"
        )

    # Test 12: truncated flag
    def test_bfs_truncated_when_max_results_exceeded(self, tmp_path):
        """max_results=2 with enough edges → truncated=True, len(related)<=2."""
        conn, files, _ = self._build_db_with_edges(tmp_path)
        result = graph.bfs_expand(
            conn, "myrepo", str(files["app"]), depth=3, max_results=2
        )
        conn.close()

        assert result["truncated"] is True, (
            "truncated should be True when max_results is exceeded"
        )
        assert len(result["related"]) <= 2, (
            f"related length should be <=2, got {len(result['related'])}"
        )

    # Test 13: target_not_found
    def test_bfs_target_not_found(self, tmp_path):
        """Unknown target → error='target_not_found', related=[]."""
        import indexer

        config = _make_config(tmp_path)
        conn = indexer.get_db(config)
        result = graph.bfs_expand(conn, "myrepo", "nonexistent/file.py", depth=1)
        conn.close()

        assert result["error"] == "target_not_found", (
            f"Expected 'target_not_found', got {result['error']}"
        )
        assert result["related"] == [], f"Expected [], got {result['related']}"

    def test_bfs_target_not_found_return_shape(self, tmp_path):
        """target_not_found result has all required keys."""
        import indexer

        config = _make_config(tmp_path)
        conn = indexer.get_db(config)
        result = graph.bfs_expand(conn, "myrepo", "ghost.py", depth=1)
        conn.close()

        for key in ("target", "depth", "related", "truncated", "error"):
            assert key in result, f"Missing key '{key}' in bfs_expand result"

    # Test 14: external node is a terminal (not expanded)
    def test_bfs_external_node_not_expanded(self, tmp_path):
        """'os' external node appears in related but triggers no further expansion."""
        conn, files, _ = self._build_db_with_edges(tmp_path)
        # Start from app.py which imports 'os' (external)
        result = graph.bfs_expand(conn, "myrepo", str(files["app"]), depth=2)
        conn.close()

        related = result["related"]
        # Find 'os' in related
        os_nodes = [r for r in related if r["value"] == "os" and r["kind"] == "external"]
        if os_nodes:
            os_depth = os_nodes[0]["depth"]
            # No node should have 'os' as its 'via' field (which would mean
            # we expanded from 'os')
            expanded_from_os = [r for r in related if r.get("via") == "os"]
            assert expanded_from_os == [], (
                f"External node 'os' should not be expanded. "
                f"Found nodes with via='os': {expanded_from_os}"
            )

    def test_bfs_return_schema(self, tmp_path):
        """bfs_expand always returns a dict with the required schema keys."""
        conn, files, _ = self._build_db_with_edges(tmp_path)
        result = graph.bfs_expand(conn, "myrepo", str(files["app"]), depth=1)
        conn.close()

        assert isinstance(result, dict)
        assert "target" in result
        assert "depth" in result
        assert "related" in result
        assert "truncated" in result
        assert "error" in result

        for item in result["related"]:
            for key in ("kind", "value", "edge_type", "depth", "via", "lineno"):
                assert key in item, f"Missing key '{key}' in related item: {item}"


# ---------------------------------------------------------------------------
# Test 15: watcher edges sync (red — _apply does not yet update edges table)
# ---------------------------------------------------------------------------

class TestWatcherEdgesSync:
    """These tests use watcher.RepoWatcher._apply directly.

    Covers watcher._apply keeping the edges table in sync (FR-006).
    The import of watcher succeeds (watcher.py exists); the assertions will fail.
    """

    def test_watcher_apply_modified_adds_import_edge(self, tmp_path):
        """After _apply({path: 'modified'}), the new import edge appears in edges."""
        import indexer
        import watcher as watcher_mod

        config = _make_config(tmp_path)
        # Override storage path to absolute so indexer.get_db resolves correctly
        config["storage"]["path"] = str(tmp_path / "code_rag.db")

        repo_path = tmp_path / "repo"
        repo_path.mkdir(parents=True)
        py_file = repo_path / "target.py"
        py_file.write_text("import os\n", encoding="utf-8")

        # Index the initial state
        repo_cfg = {"name": "watchrepo", "path": str(repo_path)}
        indexer.index_repo(repo_cfg, config)

        # Add a new import to the file
        py_file.write_text("import os\nimport json\n", encoding="utf-8")

        # Build handler and trigger _apply directly
        handler = watcher_mod.RepoWatcher("watchrepo", repo_path, config)
        handler._lazy_init()
        handler._apply({py_file: "modified"})

        conn = handler._conn
        rows = conn.execute(
            "SELECT * FROM edges WHERE repo=? AND path=? AND dst=? AND edge_type='imports'",
            ("watchrepo", str(py_file), "json"),
        ).fetchall()

        assert len(rows) >= 1, (
            "After modifying file to add 'import json', edges table should contain "
            "an imports edge with dst='json' (external). "
            "This fails because watcher._apply does not yet update the edges table."
        )

    def test_watcher_apply_deleted_removes_edges(self, tmp_path):
        """After _apply({path: 'deleted'}), that path's edges are gone."""
        import indexer
        import watcher as watcher_mod

        config = _make_config(tmp_path)
        config["storage"]["path"] = str(tmp_path / "code_rag.db")

        repo_path = tmp_path / "repo"
        repo_path.mkdir(parents=True)
        py_file = repo_path / "target.py"
        py_file.write_text("import os\nimport json\n", encoding="utf-8")

        repo_cfg = {"name": "watchrepo2", "path": str(repo_path)}
        indexer.index_repo(repo_cfg, config)

        handler = watcher_mod.RepoWatcher("watchrepo2", repo_path, config)
        handler._lazy_init()

        # First insert some edges for this path manually
        conn = handler._conn
        conn.execute(
            "INSERT INTO edges (repo, src_kind, src, dst_kind, dst, edge_type, path, lineno) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("watchrepo2", "file", str(py_file), "external", "os",
             "imports", str(py_file), 1),
        )
        conn.commit()

        # Simulate deletion
        handler._apply({py_file: "deleted"})

        remaining = conn.execute(
            "SELECT COUNT(*) AS n FROM edges WHERE repo=? AND path=?",
            ("watchrepo2", str(py_file)),
        ).fetchone()["n"]

        assert remaining == 0, (
            f"After deleting file, all edges for that path should be removed. "
            f"Got {remaining} remaining. "
            "This fails because watcher._apply does not yet clean up the edges table."
        )
