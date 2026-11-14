"""Extract a local knowledge catalog without running SQL or reading user tables."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_CATALOG = PROJECT_ROOT / "assistant_bot" / "demo_catalog.jsonl"
ORIGIN = "https://kurs.alfabank.ru"
TABLES = {
    "ArticleSubjects": ("Id", "Title", "IsPublic", "IsDeleted"),
    "Articles": (
        "Id", "Title", "Description", "CardDescription", "Url", "StatusId", "SubjectId",
        "IsPublic", "IsDeleted", "IsClosed", "IsLesson", "NoIndex", "Published",
    ),
    "ArticleEmbedItems": ("ArticleId", "Type", "HtmlContent", "Params", "Order"),
    "ContentSettings": ("Id", "EntityId", "IsDeleted"),
    "ContentSettingsIndustries": ("ContentSettingsId", "IndustryId"),
    "ContentSettingsTags": ("ContentSettingsId", "TagId"),
    "ContentSettingsTopics": ("ContentSettingsId", "TopicId"),
    "Courses": ("Id", "Title", "Description", "Url", "StatusId", "IsDeleted", "IsClosed", "NoIndex", "Published", "Cost"),
    "Industries": ("Id", "Title", "IsDeleted"),
    "Statuses": ("Id", "Title"),
    "Tags": ("Id", "Title", "IsDeleted"),
    "Topics": ("Id", "Title", "IsPublic", "IsDeleted"),
    "Documents": ("Id", "Title", "Description", "Url", "StatusId", "Published", "IsDeleted", "NoIndex"),
    "DocumentEmbedItems": ("DocumentId", "Type", "HtmlContent", "Params", "Order"),
    "Glossary": ("Id", "Title", "Description", "Url", "StatusId", "Published", "IsDeleted", "NoIndex"),
    "GlossaryEmbedItems": ("GlossaryId", "Type", "HtmlContent", "Params", "Order"),
    "Webinars": ("Id", "Title", "Description", "DescriptionExt", "Url", "StatusId", "IsDeleted", "IsClosed", "NoIndex"),
    "Podcasts": ("Id", "Title", "Description", "DescriptionExt", "Url", "StatusId", "IsDeleted", "IsClosed", "NoIndex", "Cost", "Published"),
    "Calculators": ("Id", "Title", "Description", "Url", "IsDeleted", "NoIndex"),
    "Lessons": ("Id", "Title", "Description", "IsGroup", "IsDeleted"),
    "LessonEmbedItems": ("LessonId", "Type", "HtmlContent", "Params", "Order"),
    "CourseLessonBindings": ("CourseId", "LessonId", "IsDeleted", "IsClosed", "ClosedUntil"),
    "CourseSubscriptions": ("CourseId", "SubscriptionId"),
    "CourseTariffBindings": ("CourseId", "Cost"),
}
TABLE_HEADER = re.compile(r'^COPY public\."(\w+)" \((.*)\) FROM stdin;$')
COPY_ESCAPE = re.compile(r"\\([0-7]{1,3}|x[0-9a-fA-F]{1,2}|.)")
PG_ESCAPES = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}
SLUG = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,200}")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
BLOCKS = HEADINGS | {"p", "li", "div", "section", "blockquote", "br", "tr", "td", "th", "hr"}
HIDDEN = {"script", "style", "noscript", "svg", "iframe", "template"}
MAX_PASSAGE_LENGTH = 1600


class TextBlocks(HTMLParser):
    """Read visible content and preserve the section heading of each text block."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.active_heading = ""
        self.reading_heading = False
        self.hidden_depth = 0
        self.current: list[str] = []
        self.blocks: list[tuple[str, str]] = []

    def flush(self) -> None:
        text = " ".join("".join(self.current).replace("\ufeff", "").split())
        self.current.clear()
        if text:
            if self.reading_heading:
                self.active_heading = text
            else:
                self.blocks.append((self.active_heading, text))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in HIDDEN:
            self.hidden_depth += 1
        elif not self.hidden_depth and tag in BLOCKS:
            self.flush()
            if tag in HEADINGS:
                self.reading_heading = True

    def handle_endtag(self, tag: str) -> None:
        if tag in HIDDEN and self.hidden_depth:
            self.hidden_depth -= 1
        elif not self.hidden_depth and tag in BLOCKS:
            self.flush()
            if tag in HEADINGS:
                self.reading_heading = False

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.current.append(data)


def visible_text(value: str | None) -> str:
    parser = TextBlocks()
    parser.feed(value or "")
    parser.close()
    parser.flush()
    return " ".join(text for _, text in parser.blocks)


def _escape_value(match: re.Match[str]) -> str:
    token = match[1]
    if token[0] in "01234567":
        return chr(int(token, 8))
    if len(token) > 1 and token[0] == "x":
        return chr(int(token[1:], 16))
    return PG_ESCAPES.get(token, token)


def from_copy(value: str) -> str | None:
    return None if value == r"\N" else COPY_ESCAPE.sub(_escape_value, value)


def read_tables(sql_file: Path) -> dict[str, list[dict[str, str | None]]]:
    data: dict[str, list] = {}
    name = None
    indexes = {}
    with sql_file.open(encoding="utf-8") as source:
        for line in source:
            line = line.rstrip("\r\n")
            if name is not None:
                if line == r"\.":
                    name = None
                else:
                    fields = line.split("\t")
                    data[name].append({key: from_copy(fields[index]) for key, index in indexes.items()})
            elif match := TABLE_HEADER.match(line):
                name = match[1]
                if name not in TABLES:
                    raise ValueError(f"Неожиданная таблица в выгрузке: {name}.")
                columns = re.findall(r'"(\w+)"', match[2])
                indexes = {key: columns.index(key) for key in TABLES[name]}
                data[name] = []
    missing = TABLES.keys() - data.keys()
    if missing:
        raise ValueError(f"В копии отсутствуют таблицы: {', '.join(sorted(missing))}.")
    return data


def _passages(rows: list[dict[str, str | None]]) -> list[dict[str, str]]:
    blocks = TextBlocks()
    for row in sorted(rows, key=lambda row: int(row["Order"] or 0)):
        if row["Type"] == "0":
            blocks.feed(row["HtmlContent"] or "")
            blocks.flush()
            continue
        try:
            fields = json.loads(row["Params"] or "{}")
        except (ValueError, TypeError):
            continue
        if not isinstance(fields, dict):
            continue
        label = fields.get("title")
        label = visible_text(label) if isinstance(label, str) else ""
        message = fields.get("description") if row["Type"] == "2" else fields.get("text")
        items = fields.get("items")
        if row["Type"] != "4":
            items = [message]
        if isinstance(items, list):
            blocks.blocks.extend(
                (label or blocks.active_heading, visible_text(item)) for item in items if isinstance(item, str)
            )
    blocks.close()
    blocks.flush()

    passages = []
    current = ""
    heading = ""
    for label, text in blocks.blocks:
        for sentence in SENTENCE_END.split(text):
            if len(sentence) > MAX_PASSAGE_LENGTH:
                if current:
                    passages.append({"heading": heading, "text": current})
                    current = ""
                # Preserve long source blocks instead of silently deleting them.
                words = sentence.split()
                part = ""
                for word in words:
                    if part and len(part) + len(word) + 1 > MAX_PASSAGE_LENGTH:
                        passages.append({"heading": label, "text": part})
                        part = ""
                    part = f"{part} {word}".strip()
                if part:
                    passages.append({"heading": label, "text": part})
                continue
            if current and (label != heading or len(current) + len(sentence) + 1 > MAX_PASSAGE_LENGTH):
                passages.append({"heading": heading, "text": current})
                current = ""
            if sentence:
                current = f"{current} {sentence}".strip()
                heading = label
    if current:
        passages.append({"heading": heading, "text": current})
    return [part for part in passages if len(part["text"]) >= 35]


def extract_records(tables: dict[str, list[dict[str, str | None]]]) -> list[dict]:
    published_statuses = {row["Id"] for row in tables["Statuses"] if row["Title"] == "Опубликовано"}
    if not published_statuses:
        raise ValueError("Не найден статус публикации.")

    def visible(row: dict) -> bool:
        return (
            row["StatusId"] in published_statuses
            and row["IsDeleted"] == row["IsClosed"] == row["NoIndex"] == "f"
            and bool(row["Published"]) and bool(SLUG.fullmatch(row["Url"] or ""))
        )

    subjects = {
        row["Id"]: row["Title"] for row in tables["ArticleSubjects"]
        if row["IsDeleted"] == "f" and row["IsPublic"] == "t"
    }
    settings = {row["Id"]: row["EntityId"] for row in tables["ContentSettings"] if row["IsDeleted"] == "f"}
    tags = defaultdict(set)
    for vocabulary, link, identifier in (
        ("Topics", "ContentSettingsTopics", "TopicId"),
        ("Tags", "ContentSettingsTags", "TagId"),
        ("Industries", "ContentSettingsIndustries", "IndustryId"),
    ):
        labels = {
            row["Id"]: row["Title"] for row in tables[vocabulary]
            if row["IsDeleted"] == "f" and row.get("IsPublic", "t") == "t" and row["Title"]
        }
        for row in tables[link]:
            entity = settings.get(row["ContentSettingsId"])
            label = labels.get(row[identifier])
            if entity and label:
                tags[entity].add(label)

    articles = {
        row["Id"]: row for row in tables["Articles"]
        if visible(row) and row["IsPublic"] == "t" and row["IsLesson"] == "f"
    }
    article_blocks = defaultdict(list)
    for block in tables["ArticleEmbedItems"]:
        if block["ArticleId"] in articles and block["Type"] in {"0", "2", "4", "13"}:
            article_blocks[block["ArticleId"]].append(block)

    records = []
    for key, row in articles.items():
        label = subjects.get(row["SubjectId"])
        if label:
            tags[key].add(label)
        records.append({
            "id": key,
            "title": row["Title"],
            "url": f"{ORIGIN}/articles/{row['Url']}/",
            "kind": "article",
            "price": 0,
            "description": visible_text(row["CardDescription"] or row["Description"])[:230],
            "tags": sorted(tags[key]),
            "passages": _passages(article_blocks[key]),
            "published_at": row.get("Published"),
            "content_scope": "public_text",
        })
    restricted_courses = {row["CourseId"] for row in tables.get("CourseSubscriptions", [])}
    restricted_courses.update(row["CourseId"] for row in tables.get("CourseTariffBindings", [])
                              if float(row.get("Cost") or 0) > 0)
    course_by_id = {row["Id"]: row for row in tables["Courses"]}
    for row in tables["Courses"]:
        if visible(row):
            records.append({
                "id": row["Id"],
                "title": row["Title"],
                "url": f"{ORIGIN}/courses/{row['Url']}/",
                "kind": "course",
                "price": int(row["Cost"] or 0),
                "description": visible_text(row["Description"])[:200],
                "tags": sorted(tags[row["Id"]]),
                "access_type": "paid" if int(row["Cost"] or 0) > 0 else
                               "subscription" if row["Id"] in restricted_courses else "free",
                "content_scope": "description",
            })

    def is_public(row, *, status=True):
        return (row.get("IsDeleted") == "f" and row.get("NoIndex") == "f"
                and row.get("IsClosed", "f") == "f"
                and (not status or row.get("StatusId") in published_statuses)
                and bool(SLUG.fullmatch(row.get("Url") or "")))

    for table, kind, prefix, embed, parent in (
        ("Documents", "document", "documents", "DocumentEmbedItems", "DocumentId"),
        ("Glossary", "glossary", "glossary", "GlossaryEmbedItems", "GlossaryId"),
        ("Webinars", "webinar", "webinars", None, None),
        ("Podcasts", "podcast", "podcasts", None, None),
        ("Calculators", "calculator", "calculators", None, None),
    ):
        blocks_by_id = defaultdict(list)
        for block in tables.get(embed, []):
            if block["Type"] in {"0", "2", "4", "13"}:
                blocks_by_id[block[parent]].append(block)
        for row in tables.get(table, []):
            if not is_public(row, status=table != "Calculators"):
                continue
            price = int(row.get("Cost") or 0)
            description = row.get("Description") or ""
            # Media descriptions are searchable; they are never presented as transcripts.
            body = description
            if kind in {"webinar", "podcast"}:
                body += " " + (row.get("DescriptionExt") or "")
            blocks = ([{"Type": "0", "Order": "-1", "HtmlContent": body}]
                      if body.strip() else []) + blocks_by_id[row["Id"]]
            records.append({
                "id": row["Id"], "title": row["Title"], "kind": kind,
                "url": f"{ORIGIN}/{prefix}/{row['Url']}/", "price": price,
                "description": visible_text(description)[:320], "tags": sorted(tags[row["Id"]]),
                "passages": _passages(blocks) if price == 0 else [],
                "published_at": row.get("Published"),
                "content_scope": "document_description" if kind == "document" else
                                 "public_text" if embed else "description",
            })

    # A free-looking price alone does not authorize lesson text: exclude every
    # subscription, paid tariff, hidden course, and lesson shared with such a course.
    bindings = defaultdict(list)
    for binding in tables.get("CourseLessonBindings", []):
        bindings[binding["LessonId"]].append(binding)
    lesson_blocks = defaultdict(list)
    for block in tables.get("LessonEmbedItems", []):
        if block["Type"] in {"0", "2", "4", "13"}:
            lesson_blocks[block["LessonId"]].append(block)
    for lesson in tables.get("Lessons", []):
        parents = bindings[lesson["Id"]]
        if lesson["IsDeleted"] != "f" or lesson["IsGroup"] != "f" or not parents:
            continue
        allowed = all(
            b["IsDeleted"] == b["IsClosed"] == "f" and not b.get("ClosedUntil")
            and b["CourseId"] in course_by_id and visible(course_by_id[b["CourseId"]])
            and not int(course_by_id[b["CourseId"]].get("Cost") or 0)
            and b["CourseId"] not in restricted_courses
            for b in parents
        )
        if not allowed:
            continue
        course = course_by_id[parents[0]["CourseId"]]
        records.append({
            "id": lesson["Id"], "title": f"{lesson['Title']} · {course['Title']}",
            "url": f"{ORIGIN}/courses/{course['Url']}/", "kind": "lesson", "price": 0,
            "description": visible_text(lesson.get("Description"))[:320],
            "tags": sorted(tags[course["Id"]]), "passages": _passages(lesson_blocks[lesson["Id"]]),
            "content_scope": "free_lesson", "published_at": course.get("Published"),
        })
    if not any(record.get("passages") for record in records):
        raise ValueError("В выгрузке нет разрешённых текстовых материалов.")
    return records


def build_catalog(backup: Path, output: Path, pg_restore: str) -> list[dict]:
    if backup.resolve() == output.resolve():
        raise ValueError("Файл каталога должен отличаться от файла резервной копии.")
    with backup.open("rb") as source:
        if source.read(5) != b"PGDMP":
            raise ValueError("Файл должен быть резервной копией PostgreSQL в формате custom (PGDMP).")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Keep intermediate data with the private output and discard it after import.
    with tempfile.TemporaryDirectory(prefix=".catalog-", dir=output.parent) as temporary:
        directory = Path(temporary)
        sql = directory / "catalog.sql"
        subprocess.run(
            [
                pg_restore, "--data-only", "--schema=public", "--no-owner", "--no-privileges",
                *[f"--table={name}" for name in TABLES], f"--file={sql}", str(backup.resolve()),
            ],
            check=True,
        )
        records = extract_records(read_tables(sql))
        result = directory / "catalog.jsonl"
        with result.open("w", encoding="utf-8") as target:
            target.write(json.dumps({"format": 1, "mode": "case"}) + "\n")
            for record in records:
                target.write(json.dumps(record, ensure_ascii=False) + "\n")
        result.replace(output)
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--backup", type=Path, help="Оригинальный архив из папки организаторов.")
    mode.add_argument("--demo", action="store_true", help="Скопировать синтетический каталог примеров.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--pg-restore", default="pg_restore", help="Имя или путь к pg_restore 17+.")
    args = parser.parse_args()
    try:
        if args.demo:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            if DEMO_CATALOG.resolve() != args.output.resolve():
                shutil.copyfile(DEMO_CATALOG, args.output)
            print(f"Демонстрационный каталог: {args.output}")
            return
        records = build_catalog(args.backup, args.output, args.pg_restore)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Ошибка сборки каталога: {error}\n")
    articles = sum(record["kind"] == "article" for record in records)
    evidence_articles = sum(bool(record.get("passages")) for record in records)
    paid = sum(record["price"] > 0 for record in records)
    print(
        f"Каталог: {articles} статей ({evidence_articles} с текстом), "
        f"всего {len(records)} материалов ({paid} с положительной ценой). Файл: {args.output}"
    )


if __name__ == "__main__":
    main()
