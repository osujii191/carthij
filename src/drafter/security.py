import ipaddress
import os
from typing import Optional
from urllib.parse import quote, urlparse

DEFAULT_DEMO_PORTAL_BASE = "http://127.0.0.1:3000"

_BLOCKED_HOSTS = {
    "metadata.google.internal",
    "metadata.goog",
    "169.254.169.254",
}


def get_demo_portal_base_url() -> str:
    return os.getenv("DEMO_PORTAL_BASE_URL", DEFAULT_DEMO_PORTAL_BASE).rstrip("/")


def default_demo_portal_url(program_name: str = "") -> str:
    base = get_demo_portal_base_url()
    if program_name:
        return f"{base}/demo-portal?program={quote(program_name)}"
    return f"{base}/demo-portal"


def _allowed_demo_origins(demo_portal_base_url: Optional[str]) -> set[str]:
    base = (demo_portal_base_url or get_demo_portal_base_url()).rstrip("/")
    parsed = urlparse(base)
    origins = set()
    if parsed.scheme and parsed.netloc:
        origins.add(f"{parsed.scheme.lower()}://{parsed.netloc.lower()}")
        # Allow localhost <-> 127.0.0.1 equivalence for local dev demo portal
        if parsed.hostname == "127.0.0.1" and parsed.port:
            origins.add(f"{parsed.scheme.lower()}://localhost:{parsed.port}")
        elif parsed.hostname == "localhost" and parsed.port:
            origins.add(f"{parsed.scheme.lower()}://127.0.0.1:{parsed.port}")
    return origins


def validate_portal_url(
    url: str,
    *,
    demo_portal_base_url: Optional[str] = None,
) -> str:
    """
    SSRF protection guard for `target_portal_url`:
    - Allows only http:// and https:// schemes.
    - Explicitly allows the configured DEMO_PORTAL_BASE_URL origin.
    - Blocks cloud metadata endpoints, loopback, link-local, and RFC1918 private IPs.
    """
    cleaned = (url or "").strip()
    if not cleaned:
        raise ValueError("target_portal_url cannot be empty.")

    parsed = urlparse(cleaned)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme '{scheme}'. Only http and https are allowed.")

    if not parsed.hostname:
        raise ValueError("target_portal_url must include a valid hostname.")

    origin = f"{scheme}://{parsed.netloc.lower()}"
    if origin in _allowed_demo_origins(demo_portal_base_url):
        return cleaned

    host = parsed.hostname.lower()
    if host in _BLOCKED_HOSTS or host == "localhost" or host.endswith(".internal"):
        raise ValueError(f"Blocked internal or metadata host: '{host}'.")

    try:
        ip = ipaddress.ip_address(host)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError(f"Blocked non-public IP address: '{host}'.")
    except ValueError as exc:
        # Re-raise our explicit security errors; ignore non-IP domain names
        if "Blocked" in str(exc):
            raise

    return cleaned
