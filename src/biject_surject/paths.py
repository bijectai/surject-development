"""Finding path arguments and guessing whether a tool changes files.

Path rules apply to every tool regardless of its name, because frameworks
name the same action differently (``write_file``, ``Write``, ``fs_write``,
``save``). We find path-like arguments by their *names*, and guess from the
tool's name whether it changes files. When the name is unclear we assume it
does: a wrong guess then means an extra flag, never a missed one.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Iterator, Mapping, Tuple

MAX_DEPTH = 4

PATH_PARAM_NAMES = frozenset({
    "path", "paths", "file", "files", "filepath", "file_path", "file_paths",
    "filename", "file_name", "filenames", "target", "target_path", "dest",
    "destination", "dest_path", "src", "source", "source_path", "dir",
    "dirname", "directory", "folder", "notebook_path", "output_path",
    "input_path", "output_file", "input_file", "old_path", "new_path",
    "from_path", "to_path", "cwd", "working_directory", "root", "uri",
})
PATH_PARAM_SUFFIXES = ("_path", "_paths", "_file", "_files", "_dir", "_dirs",
                       "_directory", "_filename", "_folder")

WRITE_WORDS = frozenset({
    "write", "writes", "edit", "edits", "save", "create", "delete", "del",
    "remove", "rm", "rmdir", "unlink", "move", "mv", "rename", "put", "patch",
    "append", "update", "overwrite", "mkdir", "makedirs", "touch", "copy",
    "cp", "upload", "insert", "replace", "modify", "set", "truncate", "chmod",
    "chown", "apply", "store", "dump", "commit", "push", "drop", "clear",
    "reset", "purge", "wipe", "format", "install", "exec", "execute", "run",
})
READ_WORDS = frozenset({
    "read", "get", "list", "ls", "view", "cat", "search", "grep", "glob",
    "find", "load", "fetch", "stat", "head", "tail", "describe", "show",
    "query", "inspect", "lookup", "exists", "info", "tree", "count", "diff",
})

_CAMEL = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")


def words(name: str) -> Tuple[str, ...]:
    """``NotebookEdit`` -> (notebook, edit); ``mcp__fs__read_file`` -> (mcp, fs, read, file)."""
    return tuple(w.lower() for part in re.split(r"[^A-Za-z0-9]+", name)
                 for w in _CAMEL.findall(part))


def changes_files(tool_name: str) -> bool:
    w = set(words(tool_name))
    if w & WRITE_WORDS:
        return True
    if w & READ_WORDS:
        return False
    return True  # unclear: assume it may change files


def is_path_param(name: Any, extra: Iterable[str] = ()) -> bool:
    if not isinstance(name, str):
        return False
    snake = "_".join(words(name))
    return (snake in PATH_PARAM_NAMES or snake.endswith(PATH_PARAM_SUFFIXES)
            or name in extra or snake in extra)


def path_args(params: Mapping[str, Any], extra: Iterable[str] = ()) -> Iterator[Tuple[str, str, str]]:
    """Yield (label, top_level_param, path) for every path-like string
    argument, including nested ones (``edits[0].path``)."""
    extra = tuple(extra)

    def walk(value: Any, label: str, top: str, is_path: bool, depth: int):
        if depth > MAX_DEPTH:
            return
        if isinstance(value, str):
            if is_path:
                path = _strip_file_uri(value)
                if path is not None:
                    yield label, top, path
        elif isinstance(value, Mapping):
            for k, v in value.items():
                yield from walk(v, f"{label}.{k}", top, is_path_param(k, extra), depth + 1)
        elif isinstance(value, (list, tuple)):
            for i, v in enumerate(value):
                yield from walk(v, f"{label}[{i}]", top, is_path, depth + 1)

    if not isinstance(params, Mapping):
        return
    for key, value in params.items():
        yield from walk(value, str(key), str(key), is_path_param(key, extra), 0)


def _strip_file_uri(value: str):
    if value.startswith("file://"):
        return value[len("file://"):]
    if "://" in value:
        return None  # a URL, not a local path
    return value
