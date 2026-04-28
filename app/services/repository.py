from __future__ import annotations

import subprocess
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def materialize_repository(
    *,
    local_path: str | None = None,
    repo_url: str | None = None,
    branch: str | None = None,
    upload_bytes: bytes | None = None,
    upload_name: str | None = None,
):
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    cleanup_needed = False

    try:
        if local_path:
            repo_root = Path(local_path).expanduser().resolve()
            if not repo_root.exists():
                raise FileNotFoundError(f"Local path does not exist: {repo_root}")
            yield repo_root
            return

        temp_dir = tempfile.TemporaryDirectory(prefix="summarisegit-")
        temp_root = Path(temp_dir.name)

        if repo_url:
            target = temp_root / "repo"
            clone_command = ["git", "clone", "--depth", "1"]
            if branch:
                clone_command.extend(["--branch", branch])
            clone_command.extend([repo_url, str(target)])
            subprocess.run(clone_command, check=True, capture_output=True, text=True)
            cleanup_needed = True
            yield target
            return

        if upload_bytes is not None and upload_name:
            archive_path = temp_root / upload_name
            archive_path.write_bytes(upload_bytes)
            extract_root = temp_root / "uploaded"
            extract_root.mkdir(parents=True, exist_ok=True)
            if zipfile.is_zipfile(archive_path):
                with zipfile.ZipFile(archive_path) as archive:
                    archive.extractall(extract_root)
            else:
                raise ValueError("Uploaded file must be a zip archive.")

            discovered = _resolve_extracted_root(extract_root)
            cleanup_needed = True
            yield discovered
            return

        raise ValueError("Provide a local path, repository URL, or zip upload.")
    finally:
        if cleanup_needed and temp_dir is not None:
            temp_dir.cleanup()


def _resolve_extracted_root(extract_root: Path) -> Path:
    entries = [entry for entry in extract_root.iterdir() if entry.name != "__MACOSX"]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return extract_root
