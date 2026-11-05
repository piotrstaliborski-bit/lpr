"""Oczyszczanie HTML z edytora: tylko bezpieczne znaczniki i atrybuty (biała lista)."""
import html
import re
from html.parser import HTMLParser

ALLOWED = {
    "p", "br", "b", "strong", "i", "em", "u", "h3", "h4", "ul", "ol", "li",
    "a", "img", "blockquote", "hr", "div", "span",
}
VOID = {"br", "hr", "img"}
DROP_CONTENT = {"script", "style", "iframe", "object", "embed", "svg", "math", "template"}
SAFE_HREF = re.compile(r"^(https?://|mailto:|tel:|/api/pliki/|#)", re.I)
SAFE_SRC = re.compile(r"^(https?://|/api/pliki/)", re.I)


class _Clean(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.stack, self.skip = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in DROP_CONTENT:
            self.skip += 1
            return
        if self.skip or tag not in ALLOWED:
            return
        a = dict(attrs)
        attr = ""
        if tag == "a":
            href = (a.get("href") or "").strip()
            if not SAFE_HREF.match(href):
                return self._ignored_open(tag)
            attr = f' href="{html.escape(href, quote=True)}"'
            if not href.startswith(("#", "/")):
                attr += ' target="_blank" rel="noopener noreferrer"'
        elif tag == "img":
            src = (a.get("src") or "").strip()
            if not SAFE_SRC.match(src):
                return
            alt = html.escape(a.get("alt") or "", quote=True)
            attr = f' src="{html.escape(src, quote=True)}" alt="{alt}"'
        self.out.append(f"<{tag}{attr}>")
        if tag not in VOID:
            self.stack.append(tag)

    def _ignored_open(self, tag):
        # niebezpieczny link: zostawiamy sam tekst, a zamknięcie znacznika ignorujemy
        self.stack.append(None)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in DROP_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag == "a" and None in self.stack:
            # zamknięcie odrzuconego linku
            i = len(self.stack) - 1 - self.stack[::-1].index(None)
            if all(t != "a" for t in self.stack[i + 1:]):
                del self.stack[i]
                return
        if tag in ALLOWED and tag not in VOID and tag in self.stack:
            while self.stack:
                t = self.stack.pop()
                if t:
                    self.out.append(f"</{t}>")
                if t == tag:
                    break

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data, quote=False))

    def result(self):
        while self.stack:
            t = self.stack.pop()
            if t:
                self.out.append(f"</{t}>")
        return "".join(self.out)


def clean_html(raw: str) -> str:
    p = _Clean()
    p.feed(raw or "")
    p.close()
    return p.result()
