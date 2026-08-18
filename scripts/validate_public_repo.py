#!/usr/bin/env python3
"""Fail-closed validation for the public Beyond 100 knowledge base."""

from __future__ import annotations

import hashlib
import json
import re
import struct
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from build_public_catalog import (
    content_revision,
    render_article_index,
    render_knowledge_index,
    title_from,
)


ROOT = Path(__file__).resolve().parents[1]
ARTICLES = ROOT / "platforms" / "wechat" / "articles"
FOLDER_RE = re.compile(r"^(?P<date>\d{8})_第(?P<id>\d{3})篇_(?P<slug>.+)$")
MIN_PUBLIC_TOPIC = 22
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
INSERT_MARKER_RE = re.compile(
    r"【插图\s*(\d+)(?:\s*｜[^】]*)?】请在此处插入"
)
CORRESPONDING_FILE_RE = re.compile(r"^对应文件：(.+\.jpg)\s*$", re.M)
ROOT_FILES = {
    ".gitattributes",
    ".gitignore",
    "CONTRIBUTING.md",
    "README.md",
    "RIGHTS.md",
    "SECURITY.md",
}
ALLOWED_PREFIXES = (
    ".github/",
    "catalog/",
    "docs/",
    "knowledge/",
    "platforms/",
    "scripts/",
)
BANNED_PARTS = {
    "AGENTS.md",
    "runtime",
    "automation",
    "ops",
    "master-template",
    "topics.json",
    "state.json",
    "decisions.md",
    "workflow_prompt.md",
}
BANNED_EXTENSIONS = {".rar", ".zip", ".7z", ".tar", ".gz", ".pem", ".key"}
TEXT_EXTENSIONS = {".md", ".json", ".py", ".yml", ".yaml", ".txt"}
TAXONOMY_FIELDS = {
    "primary_category": "primary_categories",
    "dimensions": "dimensions",
    "decision_stages": "decision_stages",
    "body_regions": "body_regions",
    "movement_tasks": "movement_tasks",
    "capacities": "capacities",
}
BANNED_TEXT = (
    "/" + "Users/",
    "file" + "://",
    "BEGIN " + "PRIVATE KEY",
    "BEGIN RSA " + "PRIVATE KEY",
    "github" + "_pat_",
    "gh" + "p_",
    "AI" + "za",
    "ya" + "29.",
    "folder" + "Id",
    "O" + "Auth",
)


def fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def tracked_files() -> list[Path]:
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if Path(top).resolve() != ROOT.resolve():
            raise subprocess.CalledProcessError(1, "git rev-parse")
        result = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        # `git ls-files --cached` also reports tracked paths that were deleted or
        # renamed in the working tree but have not been staged yet.  The public
        # publisher validates before staging so that it can fail closed without
        # altering the index.  Validate only the files that will actually remain
        # in the mirrored working tree; deletions are reviewed later from the
        # staged diff and must not be mistaken for unreadable live files.
        paths = [ROOT / item.decode("utf-8") for item in result.stdout.split(b"\0") if item]
        return [path for path in paths if path.is_file()]
    except (OSError, subprocess.CalledProcessError):
        return [path for path in ROOT.rglob("*") if path.is_file() and ".git" not in path.parts]


def jpeg_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as handle:
        if handle.read(2) != b"\xff\xd8":
            fail(f"not a JPEG: {path.relative_to(ROOT)}")
        while True:
            marker_start = handle.read(1)
            if not marker_start:
                fail(f"missing JPEG size marker: {path.relative_to(ROOT)}")
            if marker_start != b"\xff":
                continue
            marker = handle.read(1)
            while marker == b"\xff":
                marker = handle.read(1)
            if marker in {bytes([value]) for value in range(0xC0, 0xC4)} | {
                bytes([value]) for value in range(0xC5, 0xC8)
            } | {bytes([value]) for value in range(0xC9, 0xCC)} | {
                bytes([value]) for value in range(0xCD, 0xD0)
            }:
                length = struct.unpack(">H", handle.read(2))[0]
                data = handle.read(length - 2)
                height, width = struct.unpack(">HH", data[1:5])
                return width, height
            if marker in {b"\xd8", b"\xd9"}:
                continue
            length_raw = handle.read(2)
            if len(length_raw) != 2:
                fail(f"invalid JPEG segment: {path.relative_to(ROOT)}")
            length = struct.unpack(">H", length_raw)[0]
            handle.seek(length - 2, 1)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_repository_surface(paths: list[Path]) -> None:
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        if any(ord(character) < 32 for character in relative):
            fail(f"control character in public path: {relative!r}")
        if not (relative in ROOT_FILES or relative.startswith(ALLOWED_PREFIXES)):
            fail(f"path outside public allowlist: {relative}")
        if path.is_symlink():
            fail(f"symlink is not allowed: {relative}")
        if any(part in BANNED_PARTS for part in path.relative_to(ROOT).parts):
            fail(f"internal path is not allowed: {relative}")
        if path.suffix.lower() in BANNED_EXTENSIONS:
            fail(f"archive or sensitive extension is not allowed: {relative}")
        if path.stat().st_size > 95 * 1024 * 1024:
            fail(f"file exceeds 95 MiB: {relative}")
        if path.suffix.lower() in TEXT_EXTENSIONS:
            text = path.read_text(encoding="utf-8")
            for token in BANNED_TEXT:
                if token in text:
                    fail(f"private marker {token!r} in {relative}")
            if EMAIL_RE.search(text):
                fail(f"email address is not allowed in public tree: {relative}")


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def validate_article_packages() -> list[dict[str, object]]:
    packages = sorted(path for path in ARTICLES.iterdir() if path.is_dir())
    if not packages:
        fail("no article packages found")

    package_records = []
    for package in packages:
        match = FOLDER_RE.fullmatch(package.name)
        if not match:
            fail(f"invalid article directory name: {package.name}")
        if int(match.group("id")) < MIN_PUBLIC_TOPIC:
            fail(
                f"article precedes public collection boundary {MIN_PUBLIC_TOPIC:03d}: "
                f"{package.name}"
            )
        try:
            content_date = datetime.strptime(match.group("date"), "%Y%m%d").date()
        except ValueError:
            fail(f"invalid article date: {package.name}")

        entries = sorted(package.iterdir())
        if any(path.is_symlink() or not path.is_file() for path in entries):
            fail(f"nested directory or symlink in article package: {package.name}")
        if any(path.suffix.lower() not in {".md", ".jpg"} for path in entries):
            fail(f"unsupported file in article package: {package.name}")
        files = entries
        expected_prefix = f"{match.group('date')}_第{match.group('id')}篇_"
        if any(not path.name.startswith(expected_prefix) for path in files):
            fail(f"file prefix does not match article directory: {package.name}")

        markdown = [path for path in files if path.suffix.lower() == ".md"]
        jpgs = [path for path in files if path.suffix.lower() == ".jpg"]
        if len(markdown) != 2:
            fail(f"article package must contain exactly 2 Markdown files: {package.name}")
        summaries = [path for path in markdown if path.stem.endswith("_公众号摘要")]
        if len(summaries) != 1:
            fail(f"missing unique public-account summary: {package.name}")
        main_md = next(path for path in markdown if path != summaries[0])
        if summaries[0].name != f"{main_md.stem}_公众号摘要.md":
            fail(f"summary filename does not match main article: {package.name}")
        summary = summaries[0].read_text(encoding="utf-8").strip()
        if "\n" in summary or not 1 <= len(summary) <= 120:
            fail(f"summary must be one line and at most 120 characters: {package.name}")
        main_text = main_md.read_text(encoding="utf-8")
        if not main_text.strip() or "## 参考资料" in main_text:
            fail(f"main article is empty or contains a public reference list: {package.name}")
        if re.search(r"\b(TODO|TBD)\b|待补|占位", main_text, re.I):
            fail(f"placeholder text in article: {package.name}")
        if re.search(r"!\[[^\]]*\]\([^)]+\)", main_text):
            fail(f"Markdown image syntax is not allowed in copy-ready article: {package.name}")

        covers = [path for path in jpgs if "_封面_" in path.stem]
        illustrations = [path for path in jpgs if "_插图" in path.stem]
        if len(jpgs) != len(covers) + len(illustrations):
            fail(f"unrecognized JPG in article package: {package.name}")
        if len(covers) != 2 or not 3 <= len(illustrations) <= 5:
            fail(f"article package needs 2 covers and 3–5 illustrations: {package.name}")
        if len(files) != 2 + 2 + len(illustrations):
            fail(f"article package contains an extra file: {package.name}")

        expected_cover_names = {
            f"{main_md.stem}_封面_16比9.jpg",
            f"{main_md.stem}_封面_235比100.jpg",
        }
        if {path.name for path in covers} != expected_cover_names:
            fail(f"cover filename does not match main article: {package.name}")
        cover_sizes = {path.stem.rsplit("_封面_", 1)[1]: jpeg_size(path) for path in covers}
        if cover_sizes.get("16比9") != (3840, 2160):
            fail(f"invalid 16:9 cover size: {package.name}")
        if cover_sizes.get("235比100") != (5076, 2160):
            fail(f"invalid 2.35:1 cover size: {package.name}")

        illustration_matches = [
            re.fullmatch(
                re.escape(main_md.stem) + r"_插图(\d{2})_.+\.jpg", path.name
            )
            for path in illustrations
        ]
        if any(match is None for match in illustration_matches):
            fail(f"illustration filename does not match main article: {package.name}")
        expected_numbers = [f"插图{index:02d}" for index in range(1, len(illustrations) + 1)]
        actual_numbers = [
            f"插图{match.group(1)}" for match in illustration_matches if match is not None
        ]
        if actual_numbers != expected_numbers:
            fail(f"illustration numbering is not continuous: {package.name}")
        for path in illustrations:
            if jpeg_size(path) not in {(3840, 2160), (3200, 4000)}:
                fail(f"invalid illustration size: {path.relative_to(ROOT)}")

        marker_numbers = [int(number) for number in INSERT_MARKER_RE.findall(main_text)]
        expected_marker_numbers = list(range(1, len(illustrations) + 1))
        if marker_numbers != expected_marker_numbers:
            fail(f"illustration insertion markers are missing or out of order: {package.name}")
        corresponding_files = CORRESPONDING_FILE_RE.findall(main_text)
        expected_files = [path.name for path in illustrations]
        if corresponding_files != expected_files:
            fail(f"illustration file prompts do not match delivered files: {package.name}")

        package_records.append(
            {
                "id": int(match.group("id")),
                "id_text": match.group("id"),
                "content_date": content_date.isoformat(),
                "package": package,
                "article_path": relative(main_md),
                "summary_path": relative(summaries[0]),
                "summary": summary,
                "title": title_from(main_md),
                "covers": {
                    path.stem.rsplit("_封面_", 1)[1]: relative(path)
                    for path in covers
                },
                "illustrations": [relative(path) for path in illustrations],
                "artifacts": [relative(path) for path in files],
            }
        )
    return package_records


def validate_taxonomy_entry(
    entry: object, taxonomy: dict[str, object], label: str
) -> None:
    if not isinstance(entry, dict) or set(entry) != set(TAXONOMY_FIELDS):
        fail(f"invalid taxonomy fields for {label}")
    for field, taxonomy_section in TAXONOMY_FIELDS.items():
        allowed = taxonomy.get(taxonomy_section)
        if not isinstance(allowed, dict):
            fail(f"invalid taxonomy definition: {taxonomy_section}")
        value = entry[field]
        if field == "primary_category":
            if not isinstance(value, str) or value not in allowed:
                fail(f"unknown {field} for {label}: {value!r}")
            continue
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            fail(f"taxonomy field must be a string list for {label}: {field}")
        if len(value) != len(set(value)):
            fail(f"duplicate taxonomy id for {label}: {field}")
        unknown = [item for item in value if item not in allowed]
        if unknown:
            fail(f"unknown taxonomy id for {label}: {field}={unknown}")


def validate_catalog(packages: list[dict[str, object]]) -> None:
    taxonomy = json.loads((ROOT / "catalog" / "taxonomy.json").read_text(encoding="utf-8"))
    reviewed_tags = json.loads(
        (ROOT / "catalog" / "article-tags.json").read_text(encoding="utf-8")
    )
    if reviewed_tags.get("schema_version") != 1 or not isinstance(
        reviewed_tags.get("articles"), dict
    ):
        fail("invalid article-tags schema")
    tags = reviewed_tags["articles"]
    expected_tag_ids = {str(package["id_text"]) for package in packages}
    if set(tags) != expected_tag_ids:
        missing = sorted(expected_tag_ids - set(tags))
        extra = sorted(set(tags) - expected_tag_ids)
        fail(f"reviewed taxonomy ids do not match packages; missing={missing}, extra={extra}")
    for topic_id, entry in tags.items():
        validate_taxonomy_entry(entry, taxonomy, f"article {topic_id}")

    articles = json.loads((ROOT / "catalog" / "articles.json").read_text(encoding="utf-8"))
    if articles.get("schema_version") != 1 or articles.get("series") != "超越百岁":
        fail("invalid article catalog schema or series")
    if articles.get("article_count") != len(packages):
        fail("catalog article_count does not match article packages")
    records = articles.get("articles", [])
    if not isinstance(records, list):
        fail("catalog articles must be a list")
    if len({record["id"] for record in records}) != len(records):
        fail("catalog article ids must be unique")
    expected_ids = [int(package["id"]) for package in packages]
    if [record.get("id") for record in records] != expected_ids:
        fail("catalog article order or ids do not match article packages")
    expected_last_date = max(
        (str(package["content_date"]) for package in packages), default=None
    )
    if articles.get("last_content_date") != expected_last_date:
        fail("catalog last_content_date does not match article packages")

    for record, package in zip(records, packages):
        topic_id = str(package["id_text"])
        expected = {
            "stable_id": f"beyond100-{topic_id}",
            "series": "超越百岁",
            "title": package["title"],
            "content_date": package["content_date"],
            "status": "final",
            "platform": "wechat",
            "article_path": package["article_path"],
            "summary_path": package["summary_path"],
            "summary": package["summary"],
            "covers": package["covers"],
            "illustrations": package["illustrations"],
            "taxonomy": tags[topic_id],
        }
        for key, value in expected.items():
            if record.get(key) != value:
                fail(f"catalog field does not match package for article {topic_id}: {key}")

    manifest = json.loads(
        (ROOT / "catalog" / "assets-manifest.json").read_text(encoding="utf-8")
    )
    actual_artifacts = [
        artifact
        for package in packages
        for artifact in package["artifacts"]
    ]
    expected_manifest = []
    for artifact in actual_artifacts:
        path = ROOT / str(artifact)
        expected_manifest.append(
            {
                "relative_path": str(artifact),
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    if manifest.get("schema_version") != 1:
        fail("invalid asset manifest schema")
    if manifest.get("file_count") != len(expected_manifest):
        fail("asset manifest count does not match public artifacts")
    if manifest.get("files") != expected_manifest:
        fail("asset manifest is incomplete, stale, duplicated, or out of order")

    revision = content_revision(expected_manifest)
    if manifest.get("content_revision") != revision:
        fail("asset manifest content_revision is stale")
    if articles.get("content_revision") != revision:
        fail("article catalog content_revision does not match manifest")

    if (ARTICLES / "README.md").read_text(encoding="utf-8") != render_article_index(records):
        fail("public article README is stale")
    knowledge_index = ROOT / "knowledge" / "README.md"
    if knowledge_index.read_text(encoding="utf-8") != render_knowledge_index(
        records, taxonomy
    ):
        fail("knowledge README is stale")


def main() -> None:
    paths = tracked_files()
    validate_repository_surface(paths)
    packages = validate_article_packages()
    validate_catalog(packages)
    print(f"OK: {len(paths)} tracked files, {len(packages)} article packages")


if __name__ == "__main__":
    try:
        main()
    except (UnicodeDecodeError, ValueError, KeyError, StopIteration) as exc:
        fail(str(exc))
