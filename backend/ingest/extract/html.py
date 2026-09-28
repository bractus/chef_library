from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

from . import Extraction, register
from .text import decode_bytes

_DROP = ("script", "style", "nav", "header", "footer", "noscript", "template")
_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_BLOCKS = {"p", "div", "li", "section", "article", "blockquote", "pre", "tr", "dd", "dt",
           "figcaption", "table", "ul", "ol", "body"}
_WS = re.compile(r"[ \t ]+")


def _walk(node: Tag, out: list[str], buf: list[str]) -> None:
    for child in node.children:
        if isinstance(child, NavigableString):
            buf.append(str(child))
            continue
        if not isinstance(child, Tag):
            continue
        name = child.name.lower()
        if name == "br":
            buf.append("\n")
        elif name in _HEADINGS:
            _flush(out, buf)
            title = _WS.sub(" ", child.get_text(" ")).strip()
            if title:
                out.append(title)
        elif name in ("td", "th"):
            _walk(child, out, buf)
            buf.append(" ")
        elif name in _BLOCKS:
            _flush(out, buf)
            _walk(child, out, buf)
            _flush(out, buf)
        else:
            _walk(child, out, buf)


def _flush(out: list[str], buf: list[str]) -> None:
    text = "".join(buf)
    buf.clear()
    lines = [_WS.sub(" ", ln).strip() for ln in text.split("\n")]
    para = "\n".join(ln for ln in lines if ln)
    if para:
        out.append(para)


def html_to_text(markup: str) -> str:
    soup = BeautifulSoup(markup, "html.parser")
    for tag in soup.find_all(_DROP):
        tag.decompose()
    root = soup.body or soup
    out: list[str] = []
    buf: list[str] = []
    _walk(root, out, buf)
    _flush(out, buf)
    return "\n\n".join(out)


@register("html")
def extract_html(path: Path) -> Extraction:
    return Extraction(text=html_to_text(decode_bytes(path.read_bytes())))
