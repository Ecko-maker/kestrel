"""What the web console loads by itself when it renders an answer: the answer-URL policy.

A markdown image `![](url)` is fetched by the browser as soon as the answer is shown, so a URL
carrying private data leaks without a click. A link `[text](url)` (or a bare URL) is fetched only
if the user clicks it. Raw HTML such as `<img src=...>` is not rendered (react-markdown drops it).

Today's console (console/src/components/Markdown.tsx) loads every image from any host and never
loads a link by itself; the defaults below describe exactly that. A non-empty allowlist describes a
proposed defense that the console does not enforce yet (tiers-v2, unbuilt): set it only to measure
that defense, and the safety results record it.

Settings (.env): KESTREL_IMAGE_ALLOWLIST=host1,host2 (empty = every image loads; a host also
covers its subdomains), KESTREL_LINKS_AUTOLOAD=on|off (default off).
"""

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

TRUE = ("1", "on", "true", "yes")


@dataclass(frozen=True)
class AnswerPolicy:
    image_allowlist: tuple[str, ...] = ()  # hosts whose images load; empty = every image loads (today's console)
    links_autoload: bool = False  # a link loads only when clicked (today's console)

    @classmethod
    def from_env(cls) -> AnswerPolicy:
        hosts = os.getenv("KESTREL_IMAGE_ALLOWLIST", "")
        links = os.getenv("KESTREL_LINKS_AUTOLOAD", "off")
        return cls(
            tuple(sorted({h.strip().lower() for h in hosts.split(",") if h.strip()})),
            links.strip().lower() in TRUE,
        )

    def image_loads(self, url: str) -> bool:
        if not self.image_allowlist:
            return True
        host = (urlsplit(url.strip()).hostname or "").lower()
        return any(host == h or host.endswith("." + h) for h in self.image_allowlist)

    def describe(self) -> str:
        images = (
            f"images load only from {', '.join(self.image_allowlist)}"
            if self.image_allowlist
            else "images load from any host"
        )
        links = "links load by themselves" if self.links_autoload else "links load only on click"
        default = " (today's console)" if self == AnswerPolicy() else " (NOT today's console: a proposed defense)"
        return f"{images}, {links}{default}"

    def as_dict(self) -> dict[str, object]:
        return {"image_allowlist": list(self.image_allowlist), "links_autoload": self.links_autoload}
