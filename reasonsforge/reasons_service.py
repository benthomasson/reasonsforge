"""HTTP client for reasons-service push/pull."""

import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


def _resolve_config(
    url: str | None = None,
    api_key: str | None = None,
    domain_id: str | None = None,
) -> tuple[str, str, str]:
    """Resolve service config: explicit params > environment variables."""
    url = url or os.environ.get("REASONS_SERVICE_URL", "")
    api_key = api_key or os.environ.get("REASONS_SERVICE_API_KEY",
                os.environ.get("EXPERT_SERVICE_API_KEY", ""))
    domain_id = domain_id or os.environ.get("REASONS_SERVICE_DOMAIN_ID", "")

    missing = []
    if not url:
        missing.append("url (--url or REASONS_SERVICE_URL)")
    if not api_key:
        missing.append("api-key (--api-key or REASONS_SERVICE_API_KEY)")
    if not domain_id:
        missing.append("domain-id (--domain-id or REASONS_SERVICE_DOMAIN_ID)")
    if missing:
        raise RuntimeError(f"Missing required config: {', '.join(missing)}")

    return url.rstrip("/"), api_key, domain_id


def _request(url: str, api_key: str, data: bytes | None = None,
             method: str = "GET", content_type: str = "application/json",
             timeout: int = 120) -> dict:
    """Make an authenticated request to reasons-service."""
    req = Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": content_type,
            "User-Agent": "reasonsforge/1.0",
        },
        method=method,
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        if e.code == 401:
            raise RuntimeError(
                "Authentication failed. Check your REASONS_SERVICE_API_KEY."
            ) from e
        if e.code == 404:
            raise RuntimeError(
                f"Not found: {url}"
            ) from e
        body = ""
        try:
            body = e.read().decode("utf-8")
        except Exception:
            pass
        raise RuntimeError(
            f"HTTP {e.code} from {url}: {body}"
        ) from e
    except URLError as e:
        raise RuntimeError(
            f"Connection failed for {url}: {e.reason}"
        ) from e


def push_network(
    url: str, api_key: str, domain_id: str, network_path: str,
) -> dict:
    """Upload network.json or reasons.db via multipart file upload."""
    path = Path(network_path)
    if not path.exists():
        raise RuntimeError(f"File not found: {network_path}")

    content = path.read_bytes()
    filename = path.name.replace('"', '_')

    boundary = f"----ReasonsforgePush{uuid4().hex}"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n"
        f"\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()

    endpoint = f"{url}/api/domains/{domain_id}/import-reasons"
    return _request(endpoint, api_key, data=body, method="POST",
                    content_type=f"multipart/form-data; boundary={boundary}",
                    timeout=300)


def _chunked_push(
    url: str, api_key: str, items: list[dict], key: str,
    chunk_size: int = 50, progress: bool = False,
) -> dict:
    """Push items in chunks, aggregating imported/skipped counts."""
    total_imported = 0
    total_skipped = 0

    chunks = [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]
    iterator = chunks

    if progress:
        try:
            from tqdm import tqdm
            iterator = tqdm(chunks, desc=f"  Pushing {key}", unit="chunk")
        except ImportError:
            pass

    for chunk in iterator:
        payload = json.dumps({key: chunk}).encode("utf-8")
        result = _request(url, api_key, data=payload, method="POST", timeout=300)
        total_imported += result.get("imported", 0)
        total_skipped += result.get("skipped", 0)

    return {"imported": total_imported, "skipped": total_skipped}


def push_sources(
    url: str, api_key: str, domain_id: str, sources: list[dict],
    chunk_size: int = 50, progress: bool = False,
) -> dict:
    """Push sources via POST /api/domains/{domain_id}/import/sources."""
    endpoint = f"{url}/api/domains/{domain_id}/import/sources"
    return _chunked_push(endpoint, api_key, sources, "sources",
                         chunk_size=chunk_size, progress=progress)


def push_summaries(
    url: str, api_key: str, domain_id: str, summaries: list[dict],
    chunk_size: int = 50, progress: bool = False,
) -> dict:
    """Push summaries via POST /api/domains/{domain_id}/import/summaries."""
    endpoint = f"{url}/api/domains/{domain_id}/import/summaries"
    return _chunked_push(endpoint, api_key, summaries, "summaries",
                         chunk_size=chunk_size, progress=progress)
