#!/usr/bin/env python3
"""Build deterministic public article and asset catalogs."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTICLES = ROOT / "platforms" / "wechat" / "articles"
CATALOG = ROOT / "catalog"
FOLDER_RE = re.compile(r"^(?P<date>\d{8})_第(?P<id>\d{3})篇_(?P<slug>.+)$")
MIN_PUBLIC_TOPIC = 22


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def title_from(main_md: Path) -> str:
    text = main_md.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    stem = main_md.stem
    return re.sub(r"^\d{8}_第\d{3}篇_", "", stem)


def content_revision(manifest_files: list[dict[str, object]]) -> str:
    """Hash both artifact identity and content, so a rename changes the revision."""
    payload = "\n".join(
        f"{item['relative_path']}\0{item['size_bytes']}\0{item['sha256']}"
        for item in manifest_files
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def render_article_index(records: list[dict[str, object]]) -> str:
    lines = [
        "# 微信公众号文章与视觉成品",
        "",
        "每个目录同时包含公众号主文、摘要、两种比例的封面和 3–5 张正文插图。",
        "",
    ]
    current_month = None
    month_names = {
        "01": "1 月", "02": "2 月", "03": "3 月", "04": "4 月",
        "05": "5 月", "06": "6 月", "07": "7 月", "08": "8 月",
        "09": "9 月", "10": "10 月", "11": "11 月", "12": "12 月",
    }
    for record in records:
        year, month, _ = str(record["content_date"]).split("-")
        month_key = (year, month)
        if month_key != current_month:
            lines.extend([f"## {year} 年 {month_names[month]}", ""])
            current_month = month_key
        article_path = Path(str(record["article_path"])).relative_to(
            Path("platforms/wechat/articles")
        ).as_posix()
        lines.append(
            f"- [第 {int(record['id']):03d} 篇｜{record['title']}]({article_path})"
        )
    lines.extend(
        ["", "结构化索引见 [`catalog/articles.json`](../../../catalog/articles.json)。", ""]
    )
    return "\n".join(lines)


def render_knowledge_index(
    records: list[dict[str, object]], taxonomy: dict[str, object]
) -> str:
    """Render a durable reader index from the same reviewed taxonomy as the catalog."""
    lines = [
        "# 知识索引",
        "",
        "这里按读者问题组织《超越百岁》已公开收录的内容。索引由终审分类数据自动生成，与文章目录同步更新。",
        "",
        "身体区域只是入口，不是病因结论；同一篇内容可能同时涉及结构、功能、动作策略、负荷和恢复。",
        "",
        "## 方法与判断",
        "",
        "- [DOIT 方法：先读懂身体，再决定怎么练](../docs/doit-method.md)",
        "- [证据标准：我们怎样使用运动科学研究](../docs/evidence-policy.md)",
        "- [健康边界：什么时候不应继续训练判断](../docs/health-boundary.md)",
        "",
        "## 已收录主题",
        "",
    ]

    categories = taxonomy["primary_categories"]
    for category_id, category_name in categories.items():
        grouped = [
            record
            for record in records
            if record["taxonomy"]["primary_category"] == category_id
        ]
        if not grouped:
            continue
        lines.extend([f"### {category_name}", ""])
        for record in grouped:
            link = f"../{record['article_path']}"
            lines.append(
                f"- [第 {int(record['id']):03d} 篇｜{record['title']}]({link})"
            )
        lines.append("")

    movement_labels = taxonomy["movement_tasks"]
    used_tasks = {
        task
        for record in records
        for task in record["taxonomy"]["movement_tasks"]
    }
    if used_tasks:
        lines.extend(["## 按动作任务查找", ""])
        for task_id, task_name in movement_labels.items():
            if task_id not in used_tasks:
                continue
            matches = [
                record
                for record in records
                if task_id in record["taxonomy"]["movement_tasks"]
            ]
            links = "、".join(
                f"[第 {int(record['id']):03d} 篇](../{record['article_path']})"
                for record in matches
            )
            lines.append(f"- **{task_name}**：{links}")
        lines.append("")

    lines.extend(
        [
            "更完整的稳定标签定义见 [知识分类](../docs/taxonomy.md)，机器可读数据见 [`catalog/articles.json`](../catalog/articles.json)。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    overrides_path = CATALOG / "article-tags.json"
    overrides = json.loads(overrides_path.read_text(encoding="utf-8"))["articles"]
    taxonomy = json.loads((CATALOG / "taxonomy.json").read_text(encoding="utf-8"))
    records = []
    manifest_files = []

    folders = sorted(path for path in ARTICLES.iterdir() if path.is_dir())
    folder_ids = {
        match.group("id")
        for folder in folders
        if (match := FOLDER_RE.fullmatch(folder.name))
    }
    earlier_ids = sorted(
        topic_id for topic_id in folder_ids if int(topic_id) < MIN_PUBLIC_TOPIC
    )
    if earlier_ids:
        raise SystemExit(
            "article id(s) precede the public collection boundary: "
            + ", ".join(earlier_ids)
        )
    override_ids = set(overrides)
    missing_tags = sorted(folder_ids - override_ids)
    extra_tags = sorted(override_ids - folder_ids)
    if missing_tags:
        raise SystemExit(
            "missing reviewed taxonomy for article id(s): " + ", ".join(missing_tags)
        )
    if extra_tags:
        raise SystemExit(
            "taxonomy contains article id(s) without public packages: "
            + ", ".join(extra_tags)
        )

    for folder in folders:
        match = FOLDER_RE.fullmatch(folder.name)
        if not match:
            continue

        markdown = sorted(folder.glob("*.md"))
        summary_md = next(path for path in markdown if path.stem.endswith("_公众号摘要"))
        main_md = next(path for path in markdown if path != summary_md)
        covers = sorted(folder.glob("*_封面_*.jpg"))
        illustrations = sorted(folder.glob("*_插图*.jpg"))
        topic_id = match.group("id")
        tags = overrides[topic_id]

        def relative(path: Path) -> str:
            return path.relative_to(ROOT).as_posix()

        records.append(
            {
                "id": int(topic_id),
                "stable_id": f"beyond100-{topic_id}",
                "series": "超越百岁",
                "title": title_from(main_md),
                "content_date": f"{match.group('date')[:4]}-{match.group('date')[4:6]}-{match.group('date')[6:]}",
                "status": "final",
                "platform": "wechat",
                "article_path": relative(main_md),
                "summary_path": relative(summary_md),
                "summary": summary_md.read_text(encoding="utf-8").strip(),
                "covers": {path.stem.rsplit("_封面_", 1)[1]: relative(path) for path in covers},
                "illustrations": [relative(path) for path in illustrations],
                "taxonomy": tags,
            }
        )

        for path in sorted(folder.iterdir()):
            if path.is_file():
                manifest_files.append(
                    {
                        "relative_path": relative(path),
                        "size_bytes": path.stat().st_size,
                        "sha256": sha256(path),
                    }
                )

    digest = content_revision(manifest_files)
    article_payload = {
        "schema_version": 1,
        "series": "超越百岁",
        "article_count": len(records),
        "last_content_date": max(
            (str(record["content_date"]) for record in records), default=None
        ),
        "content_revision": digest,
        "articles": records,
    }
    manifest_payload = {
        "schema_version": 1,
        "file_count": len(manifest_files),
        "content_revision": digest,
        "files": manifest_files,
    }

    (CATALOG / "articles.json").write_text(
        json.dumps(article_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (CATALOG / "assets-manifest.json").write_text(
        json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    (ARTICLES / "README.md").write_text(
        render_article_index(records), encoding="utf-8"
    )
    (ROOT / "knowledge" / "README.md").write_text(
        render_knowledge_index(records, taxonomy), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
