from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import threading
from datetime import datetime, timezone
from typing import Optional

import requests

from vessel_tracker.config import Settings
from vessel_tracker.vessels import Vessel, VesselStore

logger = logging.getLogger(__name__)
OPENWATERS_VESSELS_URL = "https://ais.openwaters.io/v1/vessels"
OPENWATERS_MAX_ANONYMOUS_AREA = 80.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def start_ais(settings: Settings, store: VesselStore, demo: bool = False) -> threading.Thread:
    if demo:
        target = _run_demo
        args = (settings, store)
    elif settings.ais_source == "kystverket":
        target = _run_kystverket
        args = (settings, store)
    else:
        target = _run_openwaters
        args = (settings, store)
    thread = threading.Thread(target=target, args=args, name="ais-stream", daemon=True)
    thread.start()
    return thread


def fetch_openwaters_snapshot(settings: Settings, store: VesselStore) -> int:
    """Fetch current vessel positions once, splitting requests under the anonymous area limit."""
    bbox = settings.bbox
    lat_span = bbox.max_lat - bbox.min_lat
    lon_span = bbox.max_lon - bbox.min_lon
    if lat_span <= 0 or lon_span <= 0:
        raise ValueError("AIS snapshot requires a bounding box with positive width and height")

    max_tile_span = math.sqrt(OPENWATERS_MAX_ANONYMOUS_AREA)
    lat_tile_count = max(1, math.ceil(lat_span / max_tile_span))
    lon_tile_count = max(1, math.ceil(lon_span / max_tile_span))
    tile_height = lat_span / lat_tile_count
    tile_width = lon_span / lon_tile_count
    vessels: dict[int, Vessel] = {}

    for lat_tile in range(lat_tile_count):
        min_lat = bbox.min_lat + lat_tile * tile_height
        max_lat = bbox.max_lat if lat_tile == lat_tile_count - 1 else min_lat + tile_height
        for lon_tile in range(lon_tile_count):
            min_lon = bbox.min_lon + lon_tile * tile_width
            max_lon = bbox.max_lon if lon_tile == lon_tile_count - 1 else min_lon + tile_width
            response = requests.get(
                OPENWATERS_VESSELS_URL,
                params={"bbox": f"{min_lat},{min_lon},{max_lat},{max_lon}"},
                headers={"User-Agent": settings.user_agent, "Accept": "application/geo+json"},
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            features = payload.get("features") if isinstance(payload, dict) else None
            if not isinstance(features, list):
                raise ValueError("Open Waters AIS snapshot response has no GeoJSON features list")

            for feature in features:
                if not isinstance(feature, dict):
                    continue
                properties = feature.get("properties")
                geometry = feature.get("geometry")
                if not isinstance(properties, dict) or not isinstance(geometry, dict):
                    continue
                coordinates = geometry.get("coordinates")
                if not isinstance(coordinates, list) or len(coordinates) < 2:
                    continue
                vessel = _from_openwaters_snapshot(properties, coordinates)
                if vessel is not None:
                    vessels[vessel.mmsi] = vessel

    for vessel in vessels.values():
        store.upsert(vessel)
    logger.info("Fetched %s vessels from Open Waters snapshot", len(vessels))
    return len(vessels)


def _from_openwaters_snapshot(properties: dict, coordinates: list) -> Optional[Vessel]:
    mmsi = properties.get("mmsi")
    lat = _to_float(coordinates[1])
    lon = _to_float(coordinates[0])
    if mmsi is None or lat is None or lon is None:
        return None
    try:
        mmsi = int(mmsi)
    except (TypeError, ValueError):
        return None

    seen = properties.get("seen")
    updated_at = _now()
    if isinstance(seen, str):
        try:
            updated_at = datetime.fromisoformat(seen.replace("Z", "+00:00"))
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
        except ValueError:
            logger.warning("Ignoring invalid vessel timestamp in AIS snapshot: %r", seen)

    heading = _to_float(properties.get("heading"))
    return Vessel(
        mmsi=mmsi,
        lat=lat,
        lon=lon,
        cog=_to_float(properties.get("cog")),
        sog=_to_float(properties.get("sog")),
        heading=heading if heading is not None and heading < 360 else None,
        name=properties.get("name"),
        ship_type=str(properties.get("type") or "") or None,
        destination=(str(properties.get("destination") or "").strip() or None),
        updated_at=updated_at,
    )


def _run_openwaters(settings: Settings, store: VesselStore) -> None:
    asyncio.run(_openwaters_loop(settings, store))


async def _openwaters_loop(settings: Settings, store: VesselStore) -> None:
    import websockets

    subscribe = {
        "type": "subscribe",
        "bbox": settings.bbox.as_openwaters(),
        "snapshot": True,
    }
    while True:
        try:
            async with websockets.connect(
                settings.openwaters_url,
                ping_interval=20,
                ping_timeout=20,
                max_size=2**22,
            ) as ws:
                await ws.send(json.dumps(subscribe))
                logger.info("Sent Open Waters AIS subscription")
                async for raw in ws:
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if payload.get("type") == "error":
                        logger.error(
                            "Open Waters AIS rejected the subscription: %s",
                            payload.get("error", "unknown error"),
                        )
                        continue
                    vessel = _from_openwaters(payload)
                    if vessel is not None and settings.bbox.contains(vessel.lat, vessel.lon):
                        store.upsert(vessel)
        except Exception as exc:
            logger.warning("AIS stream error (%s); reconnecting in 5s", exc)
            await asyncio.sleep(5)


_SKIP_TYPES = {
    "AidsToNavigationReport",
    "BaseStationReport",
    "welcome",
    "ack",
    "error",
}


def _from_openwaters(payload: dict) -> Optional[Vessel]:
    if payload.get("type") in _SKIP_TYPES:
        return None
    if payload.get("msg_type") in _SKIP_TYPES:
        return None
    event = payload
    if payload.get("type") == "event" and isinstance(payload.get("event"), dict):
        event = payload["event"]
    elif payload.get("type") not in (None, "event"):
        return None

    nested = event.get("message") if isinstance(event.get("message"), dict) else {}
    lat = _to_float(event.get("lat") or event.get("latitude") or nested.get("Latitude"))
    lon = _to_float(event.get("lon") or event.get("longitude") or nested.get("Longitude"))
    mmsi = event.get("mmsi") or nested.get("UserID")
    if lat is None or lon is None or mmsi is None:
        return None
    if abs(lat) < 0.01 and abs(lon) < 0.01:
        return None

    heading = _to_float(
        event.get("heading") or event.get("true_heading") or nested.get("TrueHeading")
    )
    cog = _to_float(event.get("cog") or event.get("course") or nested.get("Cog"))
    sog = _to_float(event.get("sog") or event.get("speed") or nested.get("Sog"))
    name = event.get("name") or nested.get("Name") or nested.get("VesselName")
    if isinstance(name, str):
        name = name.strip() or None
    return Vessel(
        mmsi=int(mmsi),
        lat=lat,
        lon=lon,
        cog=cog,
        sog=sog,
        heading=heading if heading is not None and heading < 360 else None,
        name=name,
        ship_type=str(event.get("ship_type") or nested.get("Type") or "") or None,
        destination=(
            str(event.get("destination") or nested.get("Destination") or "").strip() or None
        ),
        updated_at=_now(),
    )


def _run_kystverket(settings: Settings, store: VesselStore) -> None:
    try:
        from pyais.stream import TCPStream
    except ImportError as exc:
        raise SystemExit(
            "Kystverket mode needs pyais. Install it with a compiler, or switch ais.source to openwaters."
        ) from exc

    while True:
        try:
            logger.info(
                "Connecting to Kystverket AIS %s:%s",
                settings.kystverket_host,
                settings.kystverket_port,
            )
            for msg in TCPStream(settings.kystverket_host, port=settings.kystverket_port):
                try:
                    decoded = msg.decode()
                except Exception:
                    continue
                vessel = _from_pyais(decoded.asdict() if hasattr(decoded, "asdict") else decoded)
                if vessel is not None and settings.bbox.contains(vessel.lat, vessel.lon):
                    store.upsert(vessel)
        except Exception as exc:
            logger.warning("Kystverket error (%s); reconnecting in 5s", exc)
            import time

            time.sleep(5)


def _from_pyais(decoded: dict) -> Optional[Vessel]:
    lat = _to_float(decoded.get("lat") or decoded.get("y"))
    lon = _to_float(decoded.get("lon") or decoded.get("x"))
    mmsi = decoded.get("mmsi")
    if lat is None or lon is None or mmsi is None:
        return None
    heading = _to_float(decoded.get("heading") or decoded.get("true_heading"))
    return Vessel(
        mmsi=int(mmsi),
        lat=lat,
        lon=lon,
        cog=_to_float(decoded.get("course") or decoded.get("cog")),
        sog=_to_float(decoded.get("speed") or decoded.get("sog")),
        heading=heading if heading is not None and heading < 360 else None,
        name=(str(decoded.get("shipname") or decoded.get("name") or "").strip() or None),
        ship_type=str(decoded.get("ship_type") or decoded.get("type") or "") or None,
        destination=(str(decoded.get("destination") or "").strip() or None),
        updated_at=_now(),
    )


def _run_demo(settings: Settings, store: VesselStore) -> None:
    import time

    bbox = settings.bbox
    rng = random.Random(42)
    ships = []
    for i in range(12):
        ships.append(
            {
                "mmsi": 200000000 + i,
                "lat": rng.uniform(bbox.min_lat, bbox.max_lat),
                "lon": rng.uniform(bbox.min_lon, bbox.max_lon),
                "course": rng.uniform(0, 360),
                "name": f"DEMO {i + 1}",
            }
        )
    logger.info("Demo mode: simulating %s vessels", len(ships))
    while True:
        for ship in ships:
            ship["lat"] += 0.002 * random.uniform(-1, 1)
            ship["lon"] += 0.002 * random.uniform(-1, 1)
            ship["lat"] = min(max(ship["lat"], bbox.min_lat), bbox.max_lat)
            ship["lon"] = min(max(ship["lon"], bbox.min_lon), bbox.max_lon)
            ship["course"] = (ship["course"] + random.uniform(-8, 8)) % 360
            store.upsert(
                Vessel(
                    mmsi=ship["mmsi"],
                    lat=ship["lat"],
                    lon=ship["lon"],
                    cog=ship["course"],
                    heading=ship["course"],
                    name=ship["name"],
                    sog=8.0,
                    updated_at=_now(),
                )
            )
        time.sleep(1.5)
