from __future__ import annotations

import argparse
import logging
import time

from vessel_tracker.ais_stream import fetch_openwaters_snapshot, start_ais
from vessel_tracker.config import load_settings
from vessel_tracker.display import run_display
from vessel_tracker.osm_map import fetch_coastline
from vessel_tracker.vessels import VesselStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Live AIS vessels on an OpenStreetMap coastline.")
    parser.add_argument("--demo", action="store_true", help="Simulate vessels instead of a live AIS feed")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument("--save", metavar="PNG", help="Write one frame to a PNG and exit (e-ink smoke test)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load_settings(args.config)
    store = VesselStore()
    logging.info(
        "Loading OSM coastline for bbox (%.3f,%.3f)-(%.3f,%.3f)",
        settings.bbox.min_lat,
        settings.bbox.min_lon,
        settings.bbox.max_lat,
        settings.bbox.max_lon,
    )
    coastline = fetch_coastline(settings)
    if args.save and not args.demo and settings.ais_source == "openwaters":
        fetch_openwaters_snapshot(settings, store)
    else:
        start_ais(settings, store, demo=args.demo)
        if args.save:
            time.sleep(2)
    run_display(settings, store, coastline, save_path=args.save)


if __name__ == "__main__":
    main()
