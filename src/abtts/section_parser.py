from __future__ import annotations

import posixpath
import re
import zipfile
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import List
from xml.etree import ElementTree as ET


@dataclass(frozen=True)
class Section:
    kind: str   # "CHAPTER" | "EXTRA" | "SIDE STORY"
    title: str  # e.g. "3: The Escape" or "Bonus Scene"
    text: str   # section body


# Match lines like:
# CHAPTER 3: Name of ch
# EXTRA: Name
# SIDE STORY: Name
_HEADER_RE = re.compile(
    r"^(CHAPTER|EXTRA|SIDE STORY)\s*(.*)$",
    re.IGNORECASE,
)


def parse_sections_from_text(text: str) -> List[Section]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    # Find headers
    headers = []
    for i, line in enumerate(lines):
        m = _HEADER_RE.match(line.strip())
        if m:
            kind = m.group(1).upper()
            rest = (m.group(2) or "").strip()
            title = rest if rest else "(untitled)"
            headers.append((i, kind, title))

    # If no headers, treat whole file as one chapter
    if not headers:
        return [Section(kind="CHAPTER", title="(whole file)", text=text.strip())]

    sections: List[Section] = []
    for idx, (line_i, kind, title) in enumerate(headers):
        start = line_i + 1
        end = headers[idx + 1][0] if idx + 1 < len(headers) else len(lines)
        body = "\n".join(lines[start:end]).strip()
        sections.append(Section(kind=kind, title=title, text=body))

    return sections


class _EpubHtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_script_or_style = False
        self._in_title = False
        self._heading_depth = 0
        self._title_text: List[str] = []
        self._heading_text: List[str] = []
        self._text_parts: List[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        lower = tag.lower()
        if lower in {"script", "style"}:
            self._in_script_or_style = True
            return
        if lower == "title":
            self._in_title = True
            return
        if lower in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._heading_depth += 1
            self._text_parts.append("\n")
            return
        if lower in {"br", "p", "div", "li", "tr", "section", "article"}:
            self._text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        lower = tag.lower()
        if lower in {"script", "style"}:
            self._in_script_or_style = False
            return
        if lower == "title":
            self._in_title = False
            return
        if lower in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._heading_depth = max(0, self._heading_depth - 1)
            self._text_parts.append("\n")
            return
        if lower in {"p", "div", "li", "tr", "section", "article"}:
            self._text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_script_or_style:
            return

        text = unescape(data).strip()
        if not text:
            return

        if self._in_title:
            self._title_text.append(text)
        if self._heading_depth > 0:
            self._heading_text.append(text)
        self._text_parts.append(text + " ")

    def result(self) -> tuple[str, str]:
        raw_text = "".join(self._text_parts)
        lines = [re.sub(r"\s+", " ", line).strip() for line in raw_text.splitlines()]
        body = "\n".join([line for line in lines if line])

        heading = re.sub(r"\s+", " ", " ".join(self._heading_text)).strip()
        title = re.sub(r"\s+", " ", " ".join(self._title_text)).strip()
        return heading or title, body


class _EpubNavLabelExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._toc_nav_depth = 0
        self._in_link = False
        self._current_href: str | None = None
        self._current_text_parts: list[str] = []
        self.entries: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        lower = tag.lower()
        attrs_dict = {k.lower(): (v or "") for k, v in attrs}

        if lower == "nav":
            nav_type = (attrs_dict.get("epub:type") or attrs_dict.get("type") or "").lower()
            role = (attrs_dict.get("role") or "").lower()
            if nav_type == "toc" or role == "doc-toc":
                self._toc_nav_depth += 1
            elif self._toc_nav_depth > 0:
                self._toc_nav_depth += 1
            return

        if self._toc_nav_depth > 0 and lower == "a":
            href = attrs_dict.get("href", "").strip()
            if href:
                self._in_link = True
                self._current_href = href
                self._current_text_parts = []

    def handle_endtag(self, tag: str) -> None:
        lower = tag.lower()
        if lower == "nav" and self._toc_nav_depth > 0:
            self._toc_nav_depth -= 1
            return

        if lower == "a" and self._in_link:
            label = re.sub(r"\s+", " ", " ".join(self._current_text_parts)).strip()
            href = (self._current_href or "").strip()
            if href and label:
                self.entries.append((href, label))
            self._in_link = False
            self._current_href = None
            self._current_text_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_link and self._toc_nav_depth > 0:
            text = unescape(data).strip()
            if text:
                self._current_text_parts.append(text)


def _normalize_target_path(base_dir: str, href: str) -> str:
    normalized = href.split("#", 1)[0].strip()
    if not normalized:
        return ""
    return posixpath.normpath(posixpath.join(base_dir, normalized))


def _epub_spine_paths_and_labels(path: Path) -> tuple[list[str], dict[str, str]]:
    with zipfile.ZipFile(path, "r") as zf:
        opf_path = "META-INF/container.xml"
        if opf_path not in zf.namelist():
            opf_candidates = [name for name in zf.namelist() if name.lower().endswith(".opf")]
            if not opf_candidates:
                return [], {}
            opf_path = opf_candidates[0]
        else:
            container_root = ET.fromstring(zf.read(opf_path))
            rootfiles = container_root.findall(".//{*}rootfile")
            if not rootfiles:
                return [], {}
            opf_path = rootfiles[0].attrib.get("full-path", "").strip()
            if not opf_path:
                return [], {}

        opf_root = ET.fromstring(zf.read(opf_path))
        manifest_href_by_id: dict[str, str] = {}
        manifest_by_id: dict[str, str] = {}
        manifest_media_by_id: dict[str, str] = {}
        manifest_props_by_id: dict[str, str] = {}
        for item in opf_root.findall(".//{*}manifest/{*}item"):
            item_id = item.attrib.get("id", "").strip()
            href = item.attrib.get("href", "").strip()
            media_type = item.attrib.get("media-type", "").strip().lower()
            props = item.attrib.get("properties", "").strip().lower()
            if not item_id or not href:
                continue
            manifest_href_by_id[item_id] = href
            manifest_media_by_id[item_id] = media_type
            manifest_props_by_id[item_id] = props
            if "html" in media_type or href.lower().endswith((".xhtml", ".html", ".htm")):
                manifest_by_id[item_id] = href

        opf_dir = posixpath.dirname(opf_path)
        spine_paths: list[str] = []
        spine_ids: list[str] = []
        for itemref in opf_root.findall(".//{*}spine/{*}itemref"):
            item_id = itemref.attrib.get("idref", "").strip()
            href = manifest_by_id.get(item_id)
            if not href:
                continue
            candidate = posixpath.normpath(posixpath.join(opf_dir, href))
            if candidate in zf.namelist():
                spine_paths.append(candidate)
                spine_ids.append(item_id)

        labels_by_path: dict[str, str] = {}
        spine_root = opf_root.find(".//{*}spine")
        toc_id = (spine_root.attrib.get("toc", "").strip() if spine_root is not None else "")
        if toc_id:
            toc_href = manifest_href_by_id.get(toc_id) or ""
            toc_media = manifest_media_by_id.get(toc_id, "")
            if toc_href and "ncx" in toc_media:
                toc_full = _normalize_target_path(opf_dir, toc_href)
                if toc_full in zf.namelist():
                    ncx_root = ET.fromstring(zf.read(toc_full))
                    for nav_point in ncx_root.findall(".//{*}navPoint"):
                        label = nav_point.findtext(".//{*}navLabel/{*}text", default="").strip()
                        src = nav_point.find(".//{*}content")
                        if src is None:
                            continue
                        target = _normalize_target_path(posixpath.dirname(toc_full), src.attrib.get("src", ""))
                        if target and label and target not in labels_by_path:
                            labels_by_path[target] = re.sub(r"\s+", " ", label)

        nav_item_id = ""
        for item_id in manifest_props_by_id:
            if "nav" in manifest_props_by_id[item_id].split():
                nav_item_id = item_id
                break
        if nav_item_id:
            nav_href = manifest_href_by_id.get(nav_item_id) or ""
            nav_full = _normalize_target_path(opf_dir, nav_href)
            if nav_full in zf.namelist():
                nav_html = zf.read(nav_full).decode("utf-8", errors="ignore")
                nav_parser = _EpubNavLabelExtractor()
                nav_parser.feed(nav_html)
                for href, label in nav_parser.entries:
                    target = _normalize_target_path(posixpath.dirname(nav_full), href)
                    if target and label and target not in labels_by_path:
                        labels_by_path[target] = re.sub(r"\s+", " ", label)

        # Fill entries missing from TOC with fallbacks, preserving spine order.
        ordered_labels: dict[str, str] = {}
        for idx, spine_path in enumerate(spine_paths):
            toc_label = labels_by_path.get(spine_path, "").strip()
            if toc_label:
                ordered_labels[spine_path] = toc_label
                continue

            item_id = spine_ids[idx] if idx < len(spine_ids) else ""
            href = manifest_by_id.get(item_id, "")
            fallback = Path(href or spine_path).stem.replace("_", " ").replace("-", " ").strip()
            ordered_labels[spine_path] = fallback or f"Chapter {idx + 1}"

        return spine_paths, ordered_labels


def parse_sections_from_epub(path: str) -> List[Section]:
    p = Path(path)
    with zipfile.ZipFile(p, "r") as zf:
        spine_paths, labels_by_path = _epub_spine_paths_and_labels(p)
        if not spine_paths:
            raise ValueError("Could not find chapter content in EPUB.")

        sections: List[Section] = []
        for i, chapter_path in enumerate(spine_paths):
            raw = zf.read(chapter_path)
            html = raw.decode("utf-8", errors="ignore")

            parser = _EpubHtmlTextExtractor()
            parser.feed(html)
            title, body = parser.result()

            text = body.strip()
            if not text:
                continue

            fallback_title = Path(chapter_path).stem.replace("_", " ").replace("-", " ").strip()
            section_title = labels_by_path.get(chapter_path) or title or fallback_title or f"Chapter {i + 1}"
            sections.append(Section(kind="CHAPTER", title=section_title, text=text))

    if not sections:
        raise ValueError("EPUB has no readable chapter text.")
    return sections
