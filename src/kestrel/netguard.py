"""Private-network block for fetch__fetch (known issue KI-pending-fetch-private-addresses, decided 2026-11-03:
forbid, never a card).

Before a fetch runs, its URL's host is resolved and the call is refused if the scheme isn't http(s) or any
address the name resolves to is loopback, private (RFC 1918), link-local (the cloud metadata address
169.254.169.254 included), carrier-grade NAT (100.64.0.0/10), unique-local or link-local IPv6, unspecified,
multicast or otherwise not for the public internet. IPv4-mapped IPv6 addresses are judged by the IPv4 inside.
One private answer in a mixed DNS reply is enough to refuse.

Limits (the issue says them too): mcp-server-fetch resolves the name again on its own and follows redirects,
so a public host that redirects to a private one, or a name whose DNS answer changes between this check and
the fetch (DNS rebinding), can still reach a private address. The full fix is fetching through Kestrel itself
(connect to the checked address, no redirects) or a sandbox with no network access.
"""

import ipaddress
import socket
from collections.abc import Callable, Iterable
from urllib.parse import urlsplit

FETCH_TOOL = "fetch__fetch"
BLOCKED = "blocked: private network address"
CGNAT = ipaddress.ip_network("100.64.0.0/10")

Resolver = Callable[[str], Iterable[str]]  # host -> the addresses it resolves to


def system_resolver(host: str) -> list[str]:
    """The addresses the operating system resolves a name to (no HTTP, no connection)."""
    return sorted({str(info[4][0]) for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)})


def no_dns(host: str) -> list[str]:
    """For bench runs: their fetches go to local stubs, so names aren't looked up; literals are still checked."""
    return []


def is_blocked(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return (
        address.is_loopback
        or address.is_private  # RFC 1918, unique-local fc00::/7, 0.0.0.0/8, and other non-public blocks
        or address.is_link_local
        or address.is_unspecified
        or address.is_multicast
        or address.is_reserved
        or (isinstance(address, ipaddress.IPv4Address) and address in CGNAT)
        or not address.is_global
    )


def check_url(url: str, resolve: Resolver = system_resolver) -> str | None:
    """None when the fetch may go ahead, else the refusal shown to the model and logged."""
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in ("http", "https"):
        return f"blocked: only http and https URLs can be fetched, not {parts.scheme or 'no scheme'!r}"
    host = parts.hostname
    if not host:
        return "blocked: the URL has no host"
    if host.rstrip(".").lower() == "localhost" or host.rstrip(".").lower().endswith(".localhost"):
        return f"{BLOCKED} ({host})"
    try:
        literal = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        literal = None
    if literal is not None:
        return f"{BLOCKED} ({literal})" if is_blocked(literal) else None
    try:
        addresses = list(resolve(host))
    except OSError:
        return None  # the name doesn't resolve here; the fetch fails on its own
    for text in addresses:
        try:
            address = ipaddress.ip_address(text.split("%", 1)[0])
        except ValueError:
            continue
        if is_blocked(address):
            return f"{BLOCKED} ({host} resolves to {address})"
    return None


def blocked_call(tool_name: str, args: dict, resolve: Resolver = system_resolver) -> str | None:
    """The refusal for a fetch__fetch call that would reach a private address, else None (other tools: None)."""
    if tool_name != FETCH_TOOL:
        return None
    return check_url(str(args.get("url", "")), resolve)
