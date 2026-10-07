"""What the web console loads by itself when it renders an answer: the answer-URL policy.

A markdown image `![](url)` is fetched by the browser as soon as the answer is shown, so a URL
carrying private data leaks without a click. A link `[text](url)` (or a bare URL) is fetched only
if the user clicks it. Raw HTML such as `<img src=...>` is not rendered (react-markdown drops it).

Two consoles exist, and the bench must say which one it grades against:
- the console before tiers v2 (branch phase3/memory-safety) loads every image from any host and
  never loads a link by itself. The defaults below describe exactly that, so "before" runs need no
  setting.
- the tiers-v2 console (this branch, defense d) loads an image only from the console itself or from
  a host on KESTREL_IMAGE_ALLOWLIST (empty by default); every other image is shown as a plain link.
  The browser enforces the same rule through the CSP header (img-src 'self' + the allowlist).
  "After" runs set KESTREL_IMAGE_POLICY=allowlist to grade against it. Only after-runs set it.

Settings (.env): KESTREL_IMAGE_ALLOWLIST=host1,host2 (a host also covers its subdomains; the
console and the bench read the same list), KESTREL_IMAGE_POLICY=any|allowlist (bench only, default
any: with any and an empty list every image loads), KESTREL_LINKS_AUTOLOAD=on|off (default off).
Results record the policy; `bench compare` warns when two files differ.
"""

import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

TRUE = ("1", "on", "true", "yes")
_HOST = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$")


def image_allowlist_from_env() -> tuple[str, ...]:
    """KESTREL_IMAGE_ALLOWLIST as clean host names (anything else is dropped, so a typo can't
    inject into the CSP header)."""
    hosts = {h.strip().lower() for h in os.getenv("KESTREL_IMAGE_ALLOWLIST", "").split(",")}
    return tuple(sorted(h for h in hosts if _HOST.match(h)))


@dataclass(frozen=True)
class AnswerPolicy:
    image_allowlist: tuple[str, ...] = ()  # hosts whose images load; empty = every image loads (old console)
    links_autoload: bool = False  # a link loads only when clicked (both consoles)
    images_only_allowlisted: bool = False  # the tiers-v2 console: an empty allowlist means no outside image

    @classmethod
    def from_env(cls) -> AnswerPolicy:
        links = os.getenv("KESTREL_LINKS_AUTOLOAD", "off")
        mode = os.getenv("KESTREL_IMAGE_POLICY", "any").strip().lower()
        if mode not in ("any", "allowlist"):
            raise ValueError(f"KESTREL_IMAGE_POLICY must be 'any' or 'allowlist', not {mode!r}")
        return cls(image_allowlist_from_env(), links.strip().lower() in TRUE, mode == "allowlist")

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> AnswerPolicy:
        """From a results file's meta (files from before a field have its default)."""
        raw = raw or {}
        return cls(
            tuple(raw.get("image_allowlist", ())),
            bool(raw.get("links_autoload", False)),
            raw.get("image_policy", "any") == "allowlist",
        )

    def image_loads(self, url: str) -> bool:
        """Does the browser fetch this image from an outside host by itself?"""
        host = (urlsplit(url.strip()).hostname or "").lower()
        if self.images_only_allowlisted and not host:
            return False  # a relative URL loads from Kestrel's own console: nothing leaves the machine
        if not self.image_allowlist and not self.images_only_allowlisted:
            return True
        return any(host == h or host.endswith("." + h) for h in self.image_allowlist)

    def describe(self) -> str:
        hosts = ", ".join(self.image_allowlist)
        if self.images_only_allowlisted:
            images = f"images load only from the console itself{' and ' + hosts if hosts else ''}"
        else:
            images = f"images load only from {hosts}" if hosts else "images load from any host"
        links = "links load by themselves" if self.links_autoload else "links load only on click"
        if self == AnswerPolicy():
            which = " (the console before tiers v2)"
        elif self == AnswerPolicy(images_only_allowlisted=True):
            which = " (the tiers-v2 console)"
        else:
            which = " (a custom policy)"
        return f"{images}, {links}{which}"

    def as_dict(self) -> dict[str, object]:
        out: dict[str, object] = {"image_allowlist": list(self.image_allowlist), "links_autoload": self.links_autoload}
        if self.images_only_allowlisted:  # only after-runs carry it, so before-run files stay unchanged
            out["image_policy"] = "allowlist"
        return out
