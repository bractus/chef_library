from __future__ import annotations

import posixpath
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from . import Extraction, ExtractionError, register
from .html import html_to_text
from .text import decode_bytes

_NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "enc": "http://www.w3.org/2001/04/xmlenc#",
}
# ofuscacao de fontes embutidas nao impede a leitura do texto — todo o resto e DRM
_FONT_OBFUSCATION = {"http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC"}


def _check_drm(zf: zipfile.ZipFile) -> None:
    if "META-INF/encryption.xml" not in zf.namelist():
        return
    root = ET.fromstring(zf.read("META-INF/encryption.xml"))
    for method in root.iter(f"{{{_NS['enc']}}}EncryptionMethod"):
        if method.get("Algorithm") not in _FONT_OBFUSCATION:
            raise ExtractionError("protected", f"EPUB com DRM ({method.get('Algorithm')})")


@register("epub")
def extract_epub(path: Path) -> Extraction:
    try:
        with zipfile.ZipFile(path) as zf:
            _check_drm(zf)
            container = ET.fromstring(zf.read("META-INF/container.xml"))
            rootfile = container.find(".//c:rootfile", _NS)
            if rootfile is None:
                raise ExtractionError("corrupt", "container.xml sem rootfile")
            opf_path = rootfile.get("full-path", "")
            opf = ET.fromstring(zf.read(opf_path))
            base = posixpath.dirname(opf_path)
            manifest = {
                item.get("id"): item.get("href")
                for item in opf.iterfind(".//opf:manifest/opf:item", _NS)
            }
            parts: list[str] = []
            for ref in opf.iterfind(".//opf:spine/opf:itemref", _NS):
                href = manifest.get(ref.get("idref"))
                if not href:
                    continue
                member = posixpath.normpath(posixpath.join(base, href.split("#")[0]))
                try:
                    markup = decode_bytes(zf.read(member))
                except KeyError:
                    continue
                text = html_to_text(markup)
                if text:
                    parts.append(text)
    except ExtractionError:
        raise
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
        raise ExtractionError("corrupt", f"{type(e).__name__}: {e}") from e
    return Extraction(text="\n\n".join(parts))
