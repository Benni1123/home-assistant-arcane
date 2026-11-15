"""Build and publish a HACS release from the validated main checkout."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path("custom_components/arcane/manifest.json")


def version_tuple(version: str) -> tuple[int, int, int]:
    """Accept only stable major.minor.patch versions."""
    if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version):
        raise ValueError(f"Invalid stable version: {version}")
    return tuple(int(part) for part in version.split("."))


def next_version(current: str, tags: list[str]) -> str:
    """Keep a manually raised version, otherwise increment the highest patch."""
    current_parts = version_tuple(current)
    versions = [
        version_tuple(tag[1:])
        for tag in tags
        if re.fullmatch(r"v\d+\.\d+\.\d+", tag)
    ]
    if not versions or current_parts > max(versions):
        return current
    major, minor, patch = max(current_parts, *versions)
    return f"{major}.{minor}.{patch + 1}"


def build_archive(root: Path, version: str) -> Path:
    """Package only installation files, never caches, tests or credentials."""
    version_tuple(version)
    output = root / "dist" / f"home-assistant-arcane-{version}.zip"
    output.parent.mkdir(exist_ok=True)
    component = root / "custom_components" / "arcane"
    files = [
        path for path in component.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix not in (".pyc", ".pyo")
    ]
    files.extend(root / name for name in ("README.md", "LICENSE", "NOTICE.md", "hacs.json"))
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path, path.relative_to(root).as_posix())
    with ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Release archive failed verification")
    return output


def run(*args: str) -> str:
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def main() -> None:
    """Bump if needed, push the version/tag, then publish its verified ZIP."""
    if run("git", "branch", "--show-current") != "main":
        raise RuntimeError("Releases must run on main")
    if run("git", "status", "--porcelain"):
        raise RuntimeError("Refusing to release a modified checkout")

    manifest = json.loads((ROOT / MANIFEST).read_text(encoding="utf-8"))
    current = manifest["version"]
    version_tuple(current)
    tags = run("git", "tag", "--list").splitlines()
    current_tag = f"v{current}"
    head = run("git", "rev-parse", "HEAD")
    tag_at_head = (
        current_tag in tags
        and run("git", "rev-list", "-n", "1", current_tag) == head
    )
    if tag_at_head:
        # A rerun must not bump again; it can repair a failed release upload.
        version = current
    else:
        version = next_version(current, tags)

    if version != current:
        manifest["version"] = version
        (ROOT / MANIFEST).write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

    # Build before pushing any release metadata.
    archive = build_archive(ROOT, version)
    run("git", "config", "user.name", "github-actions[bot]")
    run("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    if version != current:
        run("git", "add", str(MANIFEST))
        run("git", "commit", "-m", f"chore: release v{version}")
        # A concurrent main update rejects this push instead of overwriting it.
        # The subsequent queued run releases the newer main checkout.
        run("git", "push", "origin", "HEAD:main")

    tag = f"v{version}"
    if not tag_at_head:
        run("git", "tag", "-a", tag, "-m", f"Arcane {version}")
        run("git", "push", "origin", tag)

    release = subprocess.run(
        ["gh", "release", "view", tag, "--json", "tagName"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if release.returncode == 0:
        run("gh", "release", "upload", tag, str(archive), "--clobber")
    else:
        run(
            "gh", "release", "create", tag, str(archive),
            "--verify-tag", "--title", f"Arcane {version}", "--generate-notes",
        )
    print(f"Published {tag}: {archive.name}")


if __name__ == "__main__":
    main()
