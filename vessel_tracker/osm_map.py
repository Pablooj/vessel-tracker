from __future__ import annotations

import hashlib
import json
import logging
from typing import List, Tuple

import requests

from vessel_tracker.config import BBox, Settings

logger = logging.getLogger(__name__)
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# lon/lat rings from OSM coastline ways
Coastline = List[List[Tuple[float, float]]]


def fetch_coastline(settings: Settings) -> Coastline:
    bbox = settings.bbox
    cache_key = hashlib.sha1(
        f"{bbox.min_lat:.5f},{bbox.min_lon:.5f},{bbox.max_lat:.5f},{bbox.max_lon:.5f}".encode()
    ).hexdigest()[:12]
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = settings.cache_dir / f"coastline_{cache_key}.json"
    if cache_path.exists():
        logger.info("Using cached OSM coastline %s", cache_path.name)
        return json.loads(cache_path.read_text(encoding="utf-8"))

    pad = 0.03
    south, west = bbox.min_lat - pad, bbox.min_lon - pad
    north, east = bbox.max_lat + pad, bbox.max_lon + pad
    query = (
        f"[out:json][timeout:90];"
        f'way["natural"="coastline"]({south},{west},{north},{east});'
        f"out geom;"
    )
    logger.info("Fetching OSM coastline from Overpass")
    response = requests.post(
        OVERPASS_URL,
        data={"data": query},
        headers={"User-Agent": settings.user_agent, "Accept": "application/json"},
        timeout=120,
    )
    response.raise_for_status()
    payload = response.json()
    lines: Coastline = []
    for element in payload.get("elements", []):
        geom = element.get("geometry") or []
        if len(geom) < 2:
            continue
        lines.append([(float(pt["lon"]), float(pt["lat"])) for pt in geom])
    if not lines:
        raise RuntimeError("Overpass returned no OSM coastline ways for this bbox")
    cache_path.write_text(json.dumps(lines), encoding="utf-8")
    logger.info("Cached %s coastline ways", len(lines))
    return lines
