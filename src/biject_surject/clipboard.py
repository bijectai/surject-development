"""Copy text to the system clipboard using the OS's own tool, no dependencies."""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import List, Optional, Tuple


def _candidates() -> List[Tuple[List[str], str]]:
    """(command, encoding) pairs to try, best first."""
    if sys.platform == "win32":
        # clip.exe reads UTF-16 reliably on every Windows version; plain
        # "utf-16" would add a byte-order mark that ends up in the paste.
        return [(["clip"], "utf-16-le")]
    if sys.platform == "darwin":
        return [(["pbcopy"], "utf-8")]
    return [
        (["wl-copy"], "utf-8"),
        (["xclip", "-selection", "clipboard"], "utf-8"),
        (["xsel", "--clipboard", "--input"], "utf-8"),
    ]


def copy(text: str) -> Optional[str]:
    """Copy ``text``. Returns the tool used, or None if none worked."""
    for cmd, encoding in _candidates():
        if shutil.which(cmd[0]) is None:
            continue
        try:
            subprocess.run(cmd, input=text.encode(encoding), check=True, timeout=5,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return cmd[0]
        except (OSError, subprocess.SubprocessError):
            continue
    return None
