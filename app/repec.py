"""Bounded, validated downloads of the DPE ReDIF catalogue from RePEc FTP."""
from __future__ import annotations

import hashlib
import re
import time
import urllib.error
import urllib.request

from .citation_data import now
from .redif import parse_redif

SOURCE = 'ftp://ftp.repec.org/opt/ReDIF/RePEc/taf/defpea/defpea.redif'
SERIES = 'RePEc:taf:defpea'
MAX_BYTES = 32 * 1024 * 1024


def validate_redif(raw: bytes) -> list[dict]:
    """Fail closed when the permissive parser would silently discard a record."""
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError('ReDIF source is empty or exceeds the 32 MiB limit')
    text = raw.decode('utf-8-sig')
    templates = re.findall(r'^Template-Type:\s*([^\r\n]+)', text, re.I | re.M)
    if not templates or any(t.strip().lower() != 'redif-article 1.0' for t in templates):
        raise ValueError('Expected only ReDIF-Article 1.0 templates')
    articles = parse_redif(text)
    if len(articles) != len(templates):
        raise ValueError('Incomplete ReDIF records: every article needs a title and handle')
    handles = [a['handle'] for a in articles]
    if len(set(handles)) != len(handles):
        raise ValueError('Duplicate article handles in ReDIF source')
    if any(not h.startswith(SERIES + ':') for h in handles):
        raise ValueError('ReDIF source contains records outside Defence and Peace Economics')
    if any(not all(a[k] for k in ('volume', 'issue', 'year')) for a in articles):
        raise ValueError('Incomplete ReDIF publication metadata')
    return articles


def source_metadata(raw: bytes, articles: list[dict], **extra) -> dict:
    return {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
            'article_count': len(articles), 'series': SERIES, **extra}


def fetch_redif() -> tuple[bytes, dict]:
    """Fetch the fixed anonymous FTP source, retrying transport failures only."""
    for attempt in range(3):
        try:
            with urllib.request.urlopen(SOURCE, timeout=20) as response:
                raw = response.read(MAX_BYTES + 1)
                modified = response.headers.get('Last-Modified')
            break
        except (OSError, urllib.error.URLError) as error:
            if attempt == 2:
                raise RuntimeError(f'RePEc FTP download failed after 3 attempts: {error}') from error
            time.sleep(attempt + 1)
    articles = validate_redif(raw)
    return raw, source_metadata(raw, articles, url=SOURCE, retrieved_at=now(), last_modified=modified)
