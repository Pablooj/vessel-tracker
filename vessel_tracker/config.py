from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config.yaml"
KM_PER_DEGREE_LATITUDE = 111.32
VESSEL_TYPE_CATEGORIES = {
    "cargo",
    "fishing",
    "high_speed_craft",
    "military",
    "other",
    "passenger",
    "pilot",
    "pleasure",
    "sailing",
    "search_and_rescue",
    "tug",
}


@dataclass(frozen=True)
class BBox:
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float

    def as_openwaters(self) -> list:
        return [[self.min_lat, self.min_lon, self.max_lat, self.max_lon]]

    def contains(self, lat: float, lon: float) -> bool:
        return self.min_lat <= lat <= self.max_lat and self.min_lon <= lon <= self.max_lon


@dataclass(frozen=True)
class Settings:
    bbox: BBox
    map_width_km: float
    ais_source: str
    openwaters_url: str
    kystverket_host: str
    kystverket_port: int
    cache_dir: Path
    user_agent: str
    width: int
    height: int
    dpi: int
    eink: bool
    refresh_ms: int
    stale_seconds: int
    show_names: bool
    vessel_types: List[str]


def load_settings(path: Path | None = None) -> Settings:
    cfg_path = path or DEFAULT_CONFIG
    raw: Dict[str, Any] = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    ais = raw["ais"]
    mp = raw["map"]
    disp = raw["display"]
    map_width_km = float(mp["width_km"])
    center_lat = float(mp["center_lat"])
    center_lon = float(mp["center_lon"])
    anchor_x_fraction = float(mp["anchor_x_fraction"])
    display_width = int(disp["width"])
    display_height = int(disp["height"])
    vessel_types = [str(value).strip().lower() for value in disp.get("vessel_types", [])]
    unsupported_types = sorted(set(vessel_types) - VESSEL_TYPE_CATEGORIES)
    if map_width_km <= 0:
        raise ValueError("map.width_km must be greater than zero")
    if display_width <= 0 or display_height <= 0:
        raise ValueError("display.width and display.height must be greater than zero")
    if not 0 <= anchor_x_fraction <= 1:
        raise ValueError("map.anchor_x_fraction must be between 0 and 1")
    if not -90 < center_lat < 90:
        raise ValueError("map.center_lat must be between -90 and 90 degrees")
    if unsupported_types:
        raise ValueError(
            "Unsupported display.vessel_types: "
            + ", ".join(unsupported_types)
            + ". Choose from: "
            + ", ".join(sorted(VESSEL_TYPE_CATEGORIES))
        )

    map_height_km = map_width_km * display_height / display_width
    lat_span = map_height_km / KM_PER_DEGREE_LATITUDE
    cos_lat = math.cos(math.radians(center_lat))
    lon_span = map_width_km / (KM_PER_DEGREE_LATITUDE * cos_lat)
    bbox = BBox(
        min_lat=center_lat - lat_span / 2,
        max_lat=center_lat + lat_span / 2,
        min_lon=center_lon - lon_span * anchor_x_fraction,
        max_lon=center_lon + lon_span * (1 - anchor_x_fraction),
    )
    return Settings(
        bbox=bbox,
        map_width_km=map_width_km,
        ais_source=str(ais["source"]).lower(),
        openwaters_url=ais["openwaters_url"],
        kystverket_host=ais["kystverket_host"],
        kystverket_port=int(ais["kystverket_port"]),
        cache_dir=ROOT / mp["cache_dir"],
        user_agent=mp["user_agent"],
        width=display_width,
        height=display_height,
        dpi=int(disp["dpi"]),
        eink=bool(disp["eink"]),
        refresh_ms=int(disp["refresh_ms"]),
        stale_seconds=int(disp["stale_seconds"]),
        show_names=bool(disp["show_names"]),
        vessel_types=vessel_types,
    )
