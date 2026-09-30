"""Small git repos standing in for GitHub in the eval tests."""

import subprocess

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
