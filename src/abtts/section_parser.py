from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List


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