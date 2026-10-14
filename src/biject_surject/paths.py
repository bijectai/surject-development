"""Finding path arguments and guessing whether a tool changes files.

Path rules apply to every tool regardless of its name, because frameworks
name the same action differently (``write_file``, ``Write``, ``fs_write``,
``save``). We find path-like arguments by their *names*, and guess from the
tool's name whether it changes files. When the name is unclear we assume it
does: a wrong guess then means an extra flag, never a missed one.
"""

from __future__ import annotations

import re
import shlex
from typing import Any, Iterable, Iterator, List, Mapping, Tuple

MAX_DEPTH = 8

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
            or snake in {"_".join(words(e)) for e in extra})


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
            for k, v in _sorted_items(value):
                yield from walk(v, f"{label}.{k}", top, is_path_param(k, extra), depth + 1)
        elif isinstance(value, (list, tuple)):
            for i, v in enumerate(value):
                yield from walk(v, f"{label}[{i}]", top, is_path, depth + 1)

    if not isinstance(params, Mapping):
        return
    for key, value in _sorted_items(params):
        yield from walk(value, str(key), str(key), is_path_param(key, extra), 0)


def _sorted_items(mapping: Mapping[Any, Any]):
    """Keys in code-point order, so results never depend on the order a
    JSON object happened to arrive in (KERNEL.md section 4.1)."""
    return sorted(((str(k), v) for k, v in mapping.items()), key=lambda kv: kv[0])


def _strip_file_uri(value: str):
    if value.startswith("file://"):
        return value[len("file://"):]
    if "://" in value:
        return None  # a URL, not a local path
    return value


# --------------------------------------------------------------------------
# Shell commands: `{"command": "cat .env"}` hides its paths inside a string.

COMMAND_PARAM_NAMES = frozenset({
    "command", "commands", "cmd", "script", "shell", "shell_command",
    "bash_command", "commandline", "command_line",
})

# Programs that change files, and git subcommands that do.
WRITE_COMMANDS = frozenset({
    "rm", "rmdir", "unlink", "shred", "mv", "cp", "rsync", "scp", "install",
    "touch", "mkdir", "tee", "truncate", "dd", "chmod", "chown", "chgrp",
    "ln", "patch", "del", "erase", "rd", "move", "copy", "ren", "rename",
    "remove-item", "move-item", "copy-item", "set-content", "out-file",
    "new-item", "add-content", "clear-content",
})
WRITE_GIT = frozenset({"checkout", "reset", "clean", "commit", "push", "rm", "mv",
                       "apply", "restore", "switch", "merge", "rebase", "stash", "gc"})
IN_PLACE_FLAGS = ("sed", "perl", "ruby")  # with -i edit files in place

_SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", "{", "}"}
_OUTPUT_REDIRECT = re.compile(r"(?<![<>=!-])\d*>>?(?!=)\s*(&?)([^\s;|&]*)")
_NULL_TARGETS = {"/dev/null", "nul", "$null"}
_REDIRECT = re.compile(r"^(?:\d*>>?|&>>?|<)")


def _split(command: str) -> List[str]:
    """shlex in POSIX mode eats backslashes, which mangles Windows paths, so
    commands containing them are split in non-POSIX mode (quotes stripped)."""
    posix = "\\" not in command
    try:
        raw = shlex.split(command, posix=posix)
    except ValueError:
        raw = command.split()
    return raw if posix else [t.strip("\"'") for t in raw]


def is_command_param(name: Any) -> bool:
    return isinstance(name, str) and "_".join(words(name)) in COMMAND_PARAM_NAMES


def command_tokens(command: str) -> List[str]:
    """Words of a shell command that could be paths, with redirect targets
    and `--opt=value` values split out. Tolerant of unbalanced quotes."""
    raw = _split(command)
    out: List[str] = []
    for tok in raw:
        tok = _REDIRECT.sub("", tok).strip(";|&()")
        if not tok or tok in _SEPARATORS:
            continue
        if tok.startswith("-"):
            if "=" not in tok:
                continue
            tok = tok.split("=", 1)[1]
        out.append(tok)
    return out


def command_changes_files(command: str) -> bool:
    for m in _OUTPUT_REDIRECT.finditer(command):
        dup, target = m.groups()
        if dup or target.lower() in _NULL_TARGETS:
            continue  # 2>&1 or > /dev/null: no file is written
        return True
    raw = _split(command)
    for i, tok in enumerate(raw):
        name = tok.rsplit("/", 1)[-1].lower()
        if name in WRITE_COMMANDS:
            return True
        if name == "git" and i + 1 < len(raw) and raw[i + 1] in WRITE_GIT:
            return True
        if name in IN_PLACE_FLAGS and any(t.startswith("-i") or t == "--in-place"
                                          for t in raw[i + 1:i + 4]):
            return True
    return False


def command_args(params: Mapping[str, Any]) -> Iterator[Tuple[str, str, str]]:
    """Yield (label, top_level_param, command) for top-level command strings."""
    if not isinstance(params, Mapping):
        return
    for key, value in _sorted_items(params):
        if is_command_param(key):
            if isinstance(value, str):
                yield str(key), str(key), value
            elif isinstance(value, (list, tuple)):
                strings = [v for v in value if isinstance(v, str)]
                if strings:
                    yield str(key), str(key), " ".join(shlex.quote(s) for s in strings)
