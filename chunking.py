from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from llama_index.core.node_parser import CodeSplitter

EXT_TO_LANG = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
}

SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "tests", "test",
}


@dataclass
class Chunk:
    id: str
    repo: str
    path: str
    symbol: str
    lineno_start: int
    lineno_end: int
    language: str
    code: str
    content_hash: str = ""
    vector: list[float] = field(default_factory=list)


def compute_content_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8", errors="ignore")).hexdigest()


def _detect_language(path: Path) -> Optional[str]:
    return EXT_TO_LANG.get(path.suffix.lower())


def _extract_symbols(source: str, language: str) -> list[tuple[str, int, int]]:
    """Return list of (symbol_name, start_line, end_line) using tree-sitter."""
    try:
        import tree_sitter_languages as tsl
        from tree_sitter import Language, Node

        lang_obj = tsl.get_language(language)
        parser = tsl.get_parser(language)
        tree = parser.parse(source.encode())

        def_patterns = {
            "python": ["function_definition", "class_definition"],
            "javascript": ["function_declaration", "class_declaration", "method_definition",
                           "arrow_function", "function_expression"],
            "typescript": ["function_declaration", "class_declaration", "method_definition",
                           "arrow_function", "function_expression"],
            "go": ["function_declaration", "method_declaration", "type_declaration"],
            "rust": ["function_item", "impl_item", "struct_item", "enum_item"],
            "java": ["method_declaration", "class_declaration"],
            "ruby": ["method", "class", "module"],
        }

        target_types = set(def_patterns.get(language, []))
        symbols = []

        def walk(node: Node):
            if node.type in target_types:
                name_node = node.child_by_field_name("name")
                if name_node:
                    name = name_node.text.decode()
                    start = node.start_point[0] + 1
                    end = node.end_point[0] + 1
                    symbols.append((name, start, end))
            for child in node.children:
                walk(child)

        walk(tree.root_node)
        return symbols
    except Exception:
        return []


def _find_enclosing_symbol(symbols: list[tuple[str, int, int]], start: int, end: int) -> str:
    best = ""
    best_size = float("inf")
    for name, sym_start, sym_end in symbols:
        if sym_start <= start and sym_end >= end:
            size = sym_end - sym_start
            if size < best_size:
                best_size = size
                best = name
    return best


def _extract_signature(
    source_lines: list[str],
    symbol_name: str,
    symbols: list[tuple[str, int, int]],
) -> str:
    for name, start, _ in symbols:
        if name == symbol_name and 1 <= start <= len(source_lines):
            return source_lines[start - 1].strip()
    return ""


def _build_header(
    rel_path: str,
    language: str,
    symbol: str,
    signature: str,
) -> str:
    lines = [f"# file: {rel_path}", f"# language: {language}"]
    if symbol:
        lines.append(f"# symbol: {symbol}")
    if signature:
        lines.append(f"# signature: {signature}")
    return "\n".join(lines) + "\n"


def chunk_file(path: Path, repo: str, config: dict, repo_root: Optional[Path] = None) -> list[Chunk]:
    language = _detect_language(path)
    if language is None:
        return []

    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    if not source.strip():
        return []

    chunk_cfg = config.get("chunking", {})
    chunk_lines = chunk_cfg.get("chunk_lines", 40)
    overlap = chunk_cfg.get("chunk_lines_overlap", 5)
    max_chars = chunk_cfg.get("max_chars", 2000)

    if repo_root is not None:
        try:
            rel_path = str(path.relative_to(repo_root))
        except ValueError:
            rel_path = str(path)
    else:
        rel_path = str(path)

    try:
        splitter = CodeSplitter(
            language=language,
            chunk_lines=chunk_lines,
            chunk_lines_overlap=overlap,
            max_chars=max_chars,
        )
        from llama_index.core import Document
        doc = Document(text=source, metadata={"file_path": str(path)})
        nodes = splitter.get_nodes_from_documents([doc])
    except Exception:
        # Fallback: split by lines
        lines = source.splitlines(keepends=True)
        nodes_text = []
        for i in range(0, len(lines), chunk_lines - overlap):
            chunk = "".join(lines[i:i + chunk_lines])
            if chunk.strip():
                nodes_text.append((chunk, i + 1, min(i + chunk_lines, len(lines))))

        symbols = _extract_symbols(source, language)
        source_lines = source.splitlines()
        chunks = []
        for code, lstart, lend in nodes_text:
            sym = _find_enclosing_symbol(symbols, lstart, lend)
            sig = _extract_signature(source_lines, sym, symbols) if sym else ""
            header = _build_header(rel_path, language, sym, sig)
            chunk_id = hashlib.sha1(f"{repo}:{path}:{lstart}:{lend}".encode()).hexdigest()
            full = header + code
            chunks.append(Chunk(
                id=chunk_id, repo=repo, path=str(path), symbol=sym,
                lineno_start=lstart, lineno_end=lend, language=language,
                code=full, content_hash=compute_content_hash(full),
            ))
        return chunks

    symbols = _extract_symbols(source, language)
    source_lines = source.splitlines()
    chunks = []

    for node in nodes:
        code = node.get_content()
        if not code.strip():
            continue

        # Locate line numbers by matching code within source
        code_stripped = code.strip()
        first_line_text = code_stripped.splitlines()[0].strip() if code_stripped else ""
        lstart = 1
        lend = len(source_lines)

        for idx, line in enumerate(source_lines, 1):
            if first_line_text and first_line_text in line:
                code_lines = code_stripped.splitlines()
                lstart = idx
                lend = min(idx + len(code_lines) - 1, len(source_lines))
                break

        sym = _find_enclosing_symbol(symbols, lstart, lend)
        sig = _extract_signature(source_lines, sym, symbols) if sym else ""
        header = _build_header(rel_path, language, sym, sig)
        chunk_id = hashlib.sha1(f"{repo}:{path}:{lstart}:{lend}".encode()).hexdigest()
        full = header + code
        chunks.append(Chunk(
            id=chunk_id, repo=repo, path=str(path), symbol=sym,
            lineno_start=lstart, lineno_end=lend, language=language,
            code=full, content_hash=compute_content_hash(full),
        ))

    return chunks


def iter_repo_files(repo_path: Path) -> list[Path]:
    files = []
    for p in repo_path.rglob("*"):
        # Skip symlinks: a link pointing outside the repo would otherwise get
        # its target indexed and leak content past the path validation layer.
        if p.is_symlink():
            continue
        if p.is_file() and not any(part in SKIP_DIRS for part in p.parts):
            if _detect_language(p) is not None:
                files.append(p)
    return files
