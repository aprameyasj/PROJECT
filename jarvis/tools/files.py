"""File tools — find, open, read, write, move, delete.

Scoped by design: searches start from the user's home / desktop / documents /
downloads (plus the JARVIS workspace), skip caches and system noise, and every
mutating operation passes through the safety gate before touching disk.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from ..core.types import Permission, ToolCategory, ToolResult
from .registry import ToolContext, tool

SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".next", ".nuxt", "dist", "build", ".cache", ".npm", ".gradle", ".idea",
    ".vscode", "target", ".tox", ".eggs", "site-packages", ".jarvis",
}
MAX_RESULTS = 200


def search_roots(ctx: ToolContext) -> list[Path]:
    home = Path.home()
    roots = [home / name for name in ("Desktop", "Documents", "Downloads", "Projects", "Code", "dev", "work")]
    roots.append(Path(ctx.settings.home))
    roots.append(home)
    seen: list[Path] = []
    for root in roots:
        if root.exists() and root not in seen:
            seen.append(root)
    return seen[:6]


def _walk(root: Path, patterns: Iterable[str], limit: int, max_depth: int = 6) -> list[dict[str, Any]]:
    needles = [p.lower() for p in patterns if p]
    found: list[dict[str, Any]] = []
    root_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda _: None):
        depth = len(Path(dirpath).parts) - root_depth
        if depth > max_depth:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for filename in filenames:
            lowered = filename.lower()
            if not any(fnmatch.fnmatch(lowered, needle) or needle in lowered for needle in needles):
                continue
            path = Path(dirpath) / filename
            try:
                stat = path.stat()
            except OSError:
                continue
            found.append(
                {
                    "path": str(path),
                    "name": filename,
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                }
            )
            if len(found) >= limit:
                return found
    return found


def _open_with_os(path: str) -> tuple[bool, str]:
    """Hand a path to the OS's default application."""
    try:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
            return True, "opened with the default Windows application"
        if os.uname().sysname == "Darwin":
            subprocess.Popen(["open", path])
            return True, "opened with the default macOS application"
        opener = shutil.which("xdg-open") or shutil.which("gio")
        if opener:
            args = [opener, path] if opener.endswith("xdg-open") else [opener, "open", path]
            subprocess.Popen(args)
            return True, "opened with xdg-open"
        return False, "no OS opener available (install xdg-utils)"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------


@tool(permission=Permission.SAFE, category=ToolCategory.FILES, examples=["Find my resume pdf"])
def find_file(ctx: ToolContext, query: str, extension: str = "", limit: int = 15,
              search_root: str = "") -> ToolResult:
    """Find files by (partial) name across the usual folders.

    Args:
        query: Part of the file name, e.g. "resume", "invoice 2026".
        extension: Optional extension filter without the dot, e.g. "pdf".
        limit: Maximum number of matches to return.
        search_root: Optional folder to search instead of the defaults.
    """
    if not query.strip():
        return ToolResult(ok=False, message="Tell me what file to look for.")

    patterns = [f"*{query.strip().lower()}*"]
    if extension:
        patterns = [f"*{query.strip().lower()}*.{extension.lstrip('.').lower()}"]

    roots = [Path(search_root).expanduser()] if search_root else search_roots(ctx)
    results: list[dict[str, Any]] = []
    for root in roots:
        if not root.exists():
            continue
        results.extend(_walk(root, patterns, limit=int(limit), max_depth=4 if root == Path.home() else 6))
        if len(results) >= limit:
            break

    results = results[: max(1, min(int(limit), 100))]
    if not results:
        return ToolResult(ok=True, message=f"No files matching “{query}”.",
                          speech=f"I couldn't find any file called {query}.",
                          data={"query": query, "results": [], "roots": [str(r) for r in roots]})

    top = results[0]["name"]
    return ToolResult(
        ok=True,
        message=f"{len(results)} match(es) for “{query}”",
        speech=f"Found {len(results)} file{'' if len(results) == 1 else 's'} matching {query}. The first is {top}.",
        data={"query": query, "results": results, "roots": [str(r) for r in roots]},
    )


@tool(permission=Permission.SAFE, category=ToolCategory.FILES)
def open_file(ctx: ToolContext, path: str) -> ToolResult:
    """Open a file or folder with the operating system's default application.

    Args:
        path: Absolute path, "~/..." path, or a file name that will be searched for.
    """
    candidate = Path(path).expanduser()
    if not candidate.exists():
        found = _walk(Path.home(), [f"*{Path(path).name.lower()}*"], limit=1, max_depth=5)
        if found:
            candidate = Path(found[0]["path"])
        else:
            return ToolResult(ok=False, message=f"`{path}` does not exist and I couldn't find a match.",
                              speech=f"I couldn't find {Path(path).name}.")
    ok, detail = _open_with_os(str(candidate))
    if not ok:
        # sandbox / headless: report what would happen
        return ToolResult(ok=False, message=f"Could not open `{candidate}`: {detail}",
                          speech="I found the file but there's no desktop to open it on.",
                          data={"path": str(candidate), "exists": True, "simulated": True})
    return ToolResult(ok=True, message=f"Opened {candidate}", speech=f"Opening {candidate.name}.",
                      data={"path": str(candidate), "detail": detail}, simulated=ctx.backend.simulated)


@tool(permission=Permission.SAFE, category=ToolCategory.FILES)
def list_directory(ctx: ToolContext, path: str = "", limit: int = 60, show_hidden: bool = False) -> ToolResult:
    """List the contents of a folder.

    Args:
        path: Folder to list. Empty = the JARVIS workspace root.
        limit: Maximum entries to return.
        show_hidden: Include dot-files.
    """
    target = Path(path).expanduser() if path else Path(ctx.settings.home).parent
    if not target.exists():
        return ToolResult(ok=False, message=f"`{target}` does not exist.")
    if not target.is_dir():
        return ToolResult(ok=False, message=f"`{target}` is a file, not a folder.")

    entries: list[dict[str, Any]] = []
    try:
        for child in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            if not show_hidden and child.name.startswith("."):
                continue
            try:
                stat = child.stat()
                entries.append({
                    "name": child.name + ("" if child.is_dir() else ""),
                    "type": "dir" if child.is_dir() else "file",
                    "size": stat.st_size if child.is_file() else None,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                    "path": str(child),
                })
            except OSError:
                continue
            if len(entries) >= max(1, min(int(limit), 500)):
                break
    except PermissionError:
        return ToolResult(ok=False, message=f"No permission to read `{target}`.")

    return ToolResult(ok=True, message=f"{len(entries)} entries in {target}",
                      speech=f"There are {len(entries)} items in {target.name}.",
                      data={"path": str(target), "entries": entries})


@tool(permission=Permission.SAFE, category=ToolCategory.FILES)
def read_file(ctx: ToolContext, path: str, max_chars: int = 6000, line_start: int = 1) -> ToolResult:
    """Read a text file's contents.

    Args:
        path: File to read.
        max_chars: Maximum characters to return.
        line_start: First line number to return (1-based).
    """
    target = Path(path).expanduser()
    if not target.is_file():
        return ToolResult(ok=False, message=f"`{path}` is not a readable file.")
    try:
        raw = target.read_text(errors="replace")
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, message=f"Could not read `{path}`: {exc}")

    lines = raw.splitlines()
    start = max(0, int(line_start) - 1)
    selected = "\n".join(lines[start:])[: max(200, int(max_chars))]
    return ToolResult(ok=True, message=f"Read {target.name} ({len(raw)} characters)",
                      speech=f"Read {target.name}.",
                      data={"path": str(target), "lines": len(lines), "text": selected,
                            "truncated": len(raw) > len(selected)})


@tool(permission=Permission.SAFE, category=ToolCategory.FILES, mutating=True)
def create_file(ctx: ToolContext, path: str, content: str = "", append: bool = False) -> ToolResult:
    """Create (or append to) a text file.

    Args:
        path: Where to write. Relative paths land in the JARVIS workspace.
        content: Text to write.
        append: Append instead of overwriting.
    """
    target = Path(path).expanduser()
    if not target.is_absolute():
        target = Path(ctx.settings.home) / "workspace" / target
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        if append:
            with open(target, "a", encoding="utf-8") as handle:
                handle.write(content)
        else:
            target.write_text(content, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, message=f"Could not write `{target}`: {exc}")
    verb = "Appended to" if append else "Wrote"
    return ToolResult(ok=True, message=f"{verb} {target} ({len(content)} characters)",
                      speech=f"Saved {target.name}.", data={"path": str(target), "chars": len(content)})


@tool(permission=Permission.CONFIRM, category=ToolCategory.FILES, mutating=True)
def move_file(ctx: ToolContext, source: str, destination: str) -> ToolResult:
    """Move or rename a file or folder.

    Args:
        source: Existing path.
        destination: New path (or a folder to move into).
    """
    src = Path(source).expanduser()
    dst = Path(destination).expanduser()
    if not src.exists():
        return ToolResult(ok=False, message=f"`{src}` does not exist.")
    try:
        if dst.is_dir():
            dst = dst / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, message=f"Move failed: {exc}")
    return ToolResult(ok=True, message=f"Moved {src} to {dst}", speech="Moved.",
                      data={"from": str(src), "to": str(dst)})


@tool(permission=Permission.CONFIRM, category=ToolCategory.FILES, mutating=True)
def delete_file(ctx: ToolContext, path: str, permanent: bool = False) -> ToolResult:
    """Delete a file. By default it is moved to the JARVIS trash folder first.

    Args:
        path: File or folder to delete.
        permanent: Skip the trash folder and delete for real.
    """
    target = Path(path).expanduser()
    if not target.exists():
        return ToolResult(ok=False, message=f"`{path}` does not exist.")

    if permanent:
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        except Exception as exc:  # noqa: BLE001
            return ToolResult(ok=False, message=f"Delete failed: {exc}")
        return ToolResult(ok=True, message=f"Permanently deleted {target}", speech="Deleted.",
                          data={"path": str(target), "permanent": True})

    trash = Path(ctx.settings.home) / "trash"
    trash.mkdir(parents=True, exist_ok=True)
    destination = trash / f"{int(datetime.now().timestamp())}-{target.name}"
    try:
        shutil.move(str(target), str(destination))
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, message=f"Could not move `{target}` to trash: {exc}")
    return ToolResult(ok=True, message=f"Moved {target.name} to the JARVIS trash ({destination})",
                      speech=f"Deleted {target.name}. It's in the trash if you need it back.",
                      data={"path": str(target), "trash": str(destination)})


@tool(permission=Permission.SAFE, category=ToolCategory.FILES)
def file_info(ctx: ToolContext, path: str) -> ToolResult:
    """Report size, type and timestamps for a file or folder.

    Args:
        path: File or folder to inspect.
    """
    target = Path(path).expanduser()
    if not target.exists():
        return ToolResult(ok=False, message=f"`{path}` does not exist.")
    stat = target.stat()
    size = stat.st_size
    if target.is_dir():
        size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
    data = {
        "path": str(target),
        "type": "directory" if target.is_dir() else "file",
        "bytes": size,
        "human_size": _human_size(size),
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        "created": datetime.fromtimestamp(stat.st_ctime).isoformat(timespec="seconds"),
        "suffix": target.suffix,
    }
    return ToolResult(ok=True, message=f"{target.name}: {data['human_size']} {data['type']}",
                      speech=f"{target.name} is {data['human_size']}.", data=data)


def _human_size(num: int) -> str:
    size = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
