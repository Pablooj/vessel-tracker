from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Dict, List, Optional


@dataclass
class Vessel:
    mmsi: int
    lat: float
    lon: float
    cog: Optional[float] = None
    sog: Optional[float] = None
    heading: Optional[float] = None
    name: Optional[str] = None
    ship_type: Optional[str] = None
    destination: Optional[str] = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def course(self) -> float:
        if self.heading is not None and 0 <= self.heading < 360:
            return self.heading
        if self.cog is not None:
            return self.cog
        return 0.0


class VesselStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._vessels: Dict[int, Vessel] = {}

    def upsert(self, vessel: Vessel) -> None:
        with self._lock:
            existing = self._vessels.get(vessel.mmsi)
            if existing is not None:
                if not vessel.name:
                    vessel.name = existing.name
                if vessel.ship_type is None:
                    vessel.ship_type = existing.ship_type
                if vessel.destination is None:
                    vessel.destination = existing.destination
            self._vessels[vessel.mmsi] = vessel

    def snapshot(self, stale_seconds: int) -> List[Vessel]:
        now = datetime.now(timezone.utc)
        with self._lock:
            return [
                v
                for v in self._vessels.values()
                if (now - v.updated_at).total_seconds() <= stale_seconds
            ]
