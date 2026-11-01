"""Git repository adapters.

Remote content is always fetched through a provider adapter (never trusted from
a webhook payload). Credentials are decrypted per operation and never logged;
clone URLs are sanitised before they reach an error message.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..config import get_settings
from ..models import Repository
from ..security import decrypt_secret, validate_outbound_url

settings = get_settings()

CREDENTIAL_RE = re.compile(r"(https?://)([^/@\s]+)@")


def sanitise_url(url: str) -> str:
    return CREDENTIAL_RE.sub(r"\1***@", url)


class GitError(RuntimeError):
    pass


def _run(args: list[str], cwd: str | None = None, timeout: int = 300, env: dict | None = None) -> str:
    proc = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, **(env or {}), "GIT_TERMINAL_PROMPT": "0"},
    )
    if proc.returncode != 0:
        raise GitError(sanitise_url((proc.stderr or proc.stdout or "").strip())[:800])
    return proc.stdout.strip()


@dataclass
class Checkout:
    path: str
    commit: str
    previous_commit: str | None
    changed_files: list[dict]
    head_branch: str


class GitSource:
    """Clones/pulls a repository into the managed storage directory."""

    def __init__(self, repository: Repository) -> None:
        self.repo = repository
        self.dir = Path(settings.repo_storage_dir) / repository.id

    def _auth_url(self) -> str:
        url = (self.repo.url or "").strip()
        if not url:
            raise GitError("Repository has no URL configured")
        if url.startswith("file://") or url.startswith("/"):
            if not settings.allow_local_repos:
                raise GitError("Local repositories are disabled by policy")
            return url
        token = decrypt_secret(self.repo.credential_enc)
        if token and url.startswith("https://"):
            host = url.split("://", 1)[1].split("/", 1)[0]
            url = f"https://x-access-token:{token}@{host}/{url.split('/', 3)[3]}"
        else:
            validate_outbound_url(url, allow_private=settings.allow_private_networks)
        return url

    def prepare(self, mode: str) -> Checkout:
        url = self._auth_url()
        self.dir.parent.mkdir(parents=True, exist_ok=True)
        previous = None
        if not (self.dir / ".git").exists():
            if self.dir.exists():
                shutil.rmtree(self.dir, ignore_errors=True)
            branch = self.repo.branch or self.repo.default_branch or "main"
            _run(["git", "clone", "--depth", "50", "--branch", branch, url, str(self.dir)],
                 timeout=settings.scan_timeout_seconds)
        else:
            _run(["git", "remote", "set-url", "origin", url], cwd=str(self.dir))
            _run(["git", "fetch", "--prune", "--depth", "100", "origin"], cwd=str(self.dir),
                 timeout=settings.scan_timeout_seconds)
            branch = self.repo.branch or "main"
            _run(["git", "checkout", "-B", branch, f"origin/{branch}"], cwd=str(self.dir))
            _run(["git", "reset", "--hard", f"origin/{branch}"], cwd=str(self.dir))

        branch = self.repo.branch or "main"
        head = _run(["git", "rev-parse", "HEAD"], cwd=str(self.dir))
        previous = self.repo.last_commit
        changed: list[dict] = []
        if previous and previous != head and mode == "incremental":
            try:
                out = _run(["git", "diff", "--name-status", previous, head], cwd=str(self.dir))
                for line in out.splitlines():
                    parts = line.split("\t")
                    if len(parts) >= 2:
                        changed.append({"status": parts[0][:1], "path": parts[-1]})
            except GitError:
                changed = []
        return Checkout(
            path=str(self.dir),
            commit=head,
            previous_commit=previous,
            changed_files=changed,
            head_branch=branch,
        )

    def file_at(self, commit: str, path: str, max_bytes: int = 400_000) -> str | None:
        try:
            out = subprocess.run(
                ["git", "show", f"{commit}:{path}"],
                cwd=str(self.dir), capture_output=True, timeout=30,
            )
            if out.returncode != 0:
                return None
            data = out.stdout[:max_bytes]
            return data.decode("utf-8", errors="replace")
        except Exception:
            return None

    def history(self, path: str, limit: int = 10) -> list[dict]:
        try:
            out = _run(
                ["git", "log", f"-{limit}", "--format=%H|%an|%aI|%s", "--", path],
                cwd=str(self.dir),
            )
        except GitError:
            return []
        commits = []
        for line in out.splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4:
                commits.append({"sha": parts[0], "author": parts[1], "date": parts[2], "message": parts[3]})
        return commits


SKIP_DIRS = {
    ".git", "node_modules", "target", "build", "dist", ".gradle", ".idea", ".mvn",
    "vendor", "__pycache__", ".venv", "venv", ".next", "coverage", ".pytest_cache",
}
LANGUAGE_BY_EXT = {
    ".java": "java",
    ".kt": "kotlin",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".py": "python",
    ".go": "go",
    ".sql": "sql",
}
CONFIG_NAMES = {
    "pom.xml", "build.gradle", "build.gradle.kts", "package.json", "go.mod",
    "application.yml", "application.yaml", "bootstrap.yml", "bootstrap.yaml",
    "application.properties", "bootstrap.properties",
}
CONFIG_RE = re.compile(r"^(application|bootstrap)(-[\w-]+)?\.(ya?ml|properties)$")


def iter_repo_files(root: str, max_bytes: int | None = None) -> list[dict]:
    """Walk a checkout, skipping vendored/build output and oversized/binary files."""
    files: list[dict] = []
    max_bytes = max_bytes or settings.max_scan_file_size
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            ext = os.path.splitext(name)[1].lower()
            is_config = name in CONFIG_NAMES or bool(CONFIG_RE.match(name)) or name.endswith(
                (".sql", ".md", ".json", ".yaml", ".yml")
            )
            if ext not in LANGUAGE_BY_EXT and not is_config:
                continue
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            if size > max_bytes:
                continue
            files.append({"path": rel, "size": size, "ext": ext,
                          "language": LANGUAGE_BY_EXT.get(ext),
                          "config": is_config})
    files.sort(key=lambda f: f["path"])
    return files


def read_text(path: str, max_bytes: int | None = None) -> str | None:
    max_bytes = max_bytes or settings.max_scan_file_size
    try:
        with open(path, "rb") as fh:
            data = fh.read(max_bytes + 1)
        if len(data) > max_bytes or b"\x00" in data[:4096]:
            return None
        return data.decode("utf-8", errors="replace")
    except OSError:
        return None
