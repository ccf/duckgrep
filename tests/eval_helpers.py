"""Small git repos standing in for GitHub in the eval tests."""

import subprocess
import threading

from bench.eval import workspace


def origin(tmp_path, files, name="origin"):
    """A committed repo at tmp_path/name; returns (path, commit)."""
    root = tmp_path / name
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(root)]
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "init"], check=True)
    return root, workspace.git("rev-parse", "HEAD", cwd=root).strip()


class Volume:
    """A cache volume that can be made unreadable, as macOS's privacy service did for a minute or two at a time."""

    def __init__(self):
        self.lost = threading.Event()

    def probe(self):
        if self.lost.is_set():
            raise PermissionError(1, "Operation not permitted")
