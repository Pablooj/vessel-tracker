from __future__ import annotations

import math
from typing import List, Set

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.animation import FuncAnimation
from matplotlib.markers import MarkerStyle
from matplotlib.offsetbox import AnnotationBbox, TextArea, VPacker
from matplotlib.path import Path
from matplotlib.transforms import Affine2D

from vessel_tracker.config import BBox, Settings
from vessel_tracker.osm_map import Coastline
from vessel_tracker.vessels import Vessel, VesselStore

_marker_artists = []
_SHIP_MARKER = Path(
    [
        (0.00, 1.00),
        (0.035, 0.98),
        (0.14, 0.86),
        (0.28, 0.52),
        (0.28, -0.82),
        (-0.28, -0.82),
        (-0.28, 0.52),
        (-0.14, 0.86),
        (-0.035, 0.98),
        (0.00, 1.00),
        (0.00, 1.00),
    ],
    [
        Path.MOVETO,
        Path.CURVE4,
        Path.CURVE4,
        Path.CURVE4,
        Path.LINETO,
        Path.LINETO,
        Path.LINETO,
        Path.CURVE4,
        Path.CURVE4,
        Path.CURVE4,
        Path.CLOSEPOLY,
    ],
)
PORT_NAMES = {
    "ESVGO": "Vigo",
    "VIGO": "Vigo",
    "GBHUL": "Hull",
    "GBLIV": "Liverpool",
    "IEGRN": "Greenore",
    "PTLEI": "Leixoes",
}
TOOLTIP_LAYOUT_TIERS_PX = (
    (52, 60, 70, 82),
    (96, 112, 132, 156, 184, 216, 248, 280),
)
TOOLTIP_LAYOUT_ANGLE_STEP = 15
LAND_PREFERENCE_PENALTY = 500
DIRECTION_PREFERENCE_WEIGHT = 45
STATIC_SPEED_THRESHOLD_KNOTS = 0.5


def run_display(
    settings: Settings,
    store: VesselStore,
    coastline: Coastline,
    save_path: str | None = None,
) -> None:
    bbox = settings.bbox
    fig_w = settings.width / settings.dpi
    fig_h = settings.height / settings.dpi
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=settings.dpi)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.set_position((0, 0, 1, 1))
    for line in coastline:
        xs = [p[0] for p in line]
        ys = [p[1] for p in line]
        ax.plot(xs, ys, color="black", linewidth=1.1, solid_capstyle="round", zorder=1)
    ax.set_xlim(bbox.min_lon, bbox.max_lon)
    ax.set_ylim(bbox.min_lat, bbox.max_lat)
    mid_lat = 0.5 * (bbox.min_lat + bbox.max_lat)
    ax.set_aspect(1.0 / math.cos(math.radians(mid_lat)))
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_axis_off()
    status = ax.text(
        0.01,
        0.02,
        "",
        transform=ax.transAxes,
        fontsize=8,
        color="black",
        va="bottom",
    )
    fig.canvas.draw()
    land_shading = _land_shading(
        coastline,
        bbox,
        max(1, round(ax.bbox.width)),
        max(1, round(ax.bbox.height)),
    )
    mainland = _largest_land_component(land_shading < 255)
    ax.imshow(
        land_shading,
        extent=(bbox.min_lon, bbox.max_lon, bbox.min_lat, bbox.max_lat),
        origin="lower",
        interpolation="nearest",
        aspect="auto",
        cmap="gray",
        vmin=0,
        vmax=255,
        zorder=0,
    )
    ax.set_aspect(1.0 / math.cos(math.radians(mid_lat)))
    vessel_labels = []

    def update(_frame: int):
        vessels = store.snapshot(settings.stale_seconds)
        visible = [
            (v.lon, v.lat, v)
            for v in vessels
            if bbox.contains(v.lat, v.lon) and _matches_vessel_types(v.ship_type, settings.vessel_types)
        ]
        _redraw_markers(ax, visible, settings)
        for label in vessel_labels:
            label.remove()
        vessel_labels.clear()
        marker_positions = [ax.transData.transform((x, y)) for x, y, _ in visible]
        occupied_boxes = []
        occupied_lines = []
        crowding = [
            (
                sum(
                    math.dist(position, other_position) < 100
                    for other_index, other_position in enumerate(marker_positions)
                    if other_index != index
                ),
                sum(
                    1 / max(math.dist(position, other_position), 1)
                    for other_index, other_position in enumerate(marker_positions)
                    if other_index != index
                ),
            )
            for index, position in enumerate(marker_positions)
        ]
        placement_order = sorted(
            range(len(visible)),
            key=lambda index: crowding[index],
            reverse=True,
        )
        outer_tier_indices = _outer_tier_label_indices(marker_positions, crowding)
        preferred_angles = []
        for position in marker_positions:
            preferred_angles.append(_preferred_label_angle(position, marker_positions, ax.bbox))
        deferred_labels = []
        for index in placement_order:
            x, y, vessel = visible[index]
            label = AnnotationBbox(
                _vessel_tooltip(vessel),
                xy=(x, y),
                xybox=(24, 0),
                xycoords="data",
                boxcoords="offset points",
                box_alignment=(0.5, 0.5),
                frameon=True,
                bboxprops={"boxstyle": "round,pad=0.3", "fc": "white", "ec": "0.45", "lw": 0.45},
                arrowprops={"arrowstyle": "-", "color": "black", "lw": 0.55},
                zorder=4,
            )
            ax.add_artist(label)
            if index in outer_tier_indices:
                deferred_labels.append((label, index))
                continue
            if _place_tooltip(
                label,
                ax,
                occupied_boxes,
                occupied_lines,
                marker_positions,
                index,
                mainland,
                preferred_angles[index],
                TOOLTIP_LAYOUT_TIERS_PX[0],
            ):
                vessel_labels.append(label)
            else:
                deferred_labels.append((label, index))
        for label, index in deferred_labels:
            if _place_tooltip(
                label,
                ax,
                occupied_boxes,
                occupied_lines,
                marker_positions,
                index,
                mainland,
                preferred_angles[index],
                TOOLTIP_LAYOUT_TIERS_PX[1],
            ):
                vessel_labels.append(label)
            else:
                label.remove()
        status.set_text(f"{len(visible)} vessels · {len(vessel_labels)} labels")
        return (status, *vessel_labels)

    if save_path:
        update(0)
        fig.savefig(save_path, dpi=settings.dpi, facecolor="white")
        plt.close(fig)
        return
    FuncAnimation(fig, update, interval=settings.refresh_ms, blit=False, cache_frame_data=False)
    plt.show()


def _matches_vessel_types(ship_type: str | None, selected_types: List[str]) -> bool:
    if not selected_types:
        return True
    if ship_type is None:
        return False

    normalized_type = ship_type.strip().lower().replace(" ", "_").replace("-", "_")
    try:
        code = int(normalized_type)
    except ValueError:
        return normalized_type in selected_types

    if code == 30:
        category = "fishing"
    elif 31 <= code <= 32 or code == 52:
        category = "tug"
    elif code == 35:
        category = "military"
    elif 33 <= code <= 34:
        category = "other"
    elif code == 36:
        category = "sailing"
    elif code == 37:
        category = "pleasure"
    elif 40 <= code <= 49:
        category = "high_speed_craft"
    elif code == 50:
        category = "pilot"
    elif code == 51:
        category = "search_and_rescue"
    elif 60 <= code <= 69:
        category = "passenger"
    elif 70 <= code <= 79:
        category = "cargo"
    elif 90 <= code <= 99:
        category = "other"
    else:
        return False
    return category in selected_types


def _vessel_tooltip(vessel: Vessel) -> VPacker:
    name = vessel.name or f"MMSI {vessel.mmsi}"
    vessel_type = _vessel_category(vessel.ship_type)
    speed = f"{vessel.sog:g} kn" if vessel.sog is not None else "Speed unknown"
    destination = _port_name(vessel.destination)
    title = TextArea(name, textprops={"fontsize": 7, "fontweight": "bold", "color": "black"})
    details = TextArea(
        f"{vessel_type} · {speed}\nTo: {destination}",
        textprops={"fontsize": 7, "color": "black", "linespacing": 1.15},
    )
    return VPacker(children=[title, details], align="center", pad=0, sep=0)


def _vessel_category(ship_type: str | None) -> str:
    if ship_type is None:
        return "Unknown type"
    normalized_type = ship_type.strip().lower().replace(" ", "_").replace("-", "_")
    try:
        code = int(normalized_type)
    except ValueError:
        return normalized_type.replace("_", " ").title()
    if 70 <= code <= 79:
        return "Cargo"
    return "Type " + str(code)


def _port_name(destination: str | None) -> str:
    if not destination:
        return "Unknown"
    normalized = "".join(character for character in destination.upper() if character.isalnum())
    return PORT_NAMES.get(normalized, destination.strip())


def _place_tooltip(
    label,
    ax,
    occupied_boxes,
    occupied_lines,
    marker_positions,
    marker_index: int,
    mainland: np.ndarray,
    preferred_angle: float,
    radii: tuple[int, ...],
) -> bool:
    renderer = ax.figure.canvas.get_renderer()
    marker_x, marker_y = marker_positions[marker_index]
    candidates = []
    points_per_pixel = 72 / ax.figure.dpi
    for radius in radii:
        for angle in range(0, 360, TOOLTIP_LAYOUT_ANGLE_STEP):
            radians = math.radians(angle)
            center = (
                marker_x + radius * math.cos(radians),
                marker_y + radius * math.sin(radians),
            )
            label.xybox = (
                (center[0] - marker_x) * points_per_pixel,
                (center[1] - marker_y) * points_per_pixel,
            )
            label.update_positions(renderer)
            extent = label.patch.get_window_extent(renderer).expanded(1.08, 1.15)
            angle_difference = abs((angle - preferred_angle + 180) % 360 - 180)
            direction_penalty = DIRECTION_PREFERENCE_WEIGHT * (
                1 - math.cos(math.radians(angle_difference))
            )
            land_fraction = _land_fraction_in_bbox(ax, extent, mainland)
            land_penalty = LAND_PREFERENCE_PENALTY * (1 - land_fraction)
            candidates.append(
                (
                    radius + direction_penalty + land_penalty,
                    radius,
                    center,
                )
            )
    candidates.sort(key=lambda candidate: (candidate[0], candidate[1]))
    for _, _, (center_x, center_y) in candidates:
        label.xybox = (
            (center_x - marker_x) * points_per_pixel,
            (center_y - marker_y) * points_per_pixel,
        )
        label.update_positions(renderer)
        extent = label.patch.get_window_extent(renderer).expanded(1.08, 1.15)
        center = extent.p0 + (extent.p1 - extent.p0) / 2
        leader = (np.asarray((marker_x, marker_y)), center)
        if (
            extent.x0 < ax.bbox.x0
            or extent.x1 > ax.bbox.x1
            or extent.y0 < ax.bbox.y0
            or extent.y1 > ax.bbox.y1
        ):
            continue
        collisions = sum(extent.overlaps(box) for box in occupied_boxes)
        collisions += sum(_segment_intersects_bbox(*leader, box) for box in occupied_boxes)
        collisions += sum(_segment_intersects_bbox(*line, extent) for line in occupied_lines)
        collisions += sum(_segments_intersect(*leader, *line) for line in occupied_lines)
        collisions += sum(
            extent.contains(*position)
            for position in marker_positions
        )
        if collisions == 0:
            occupied_boxes.append(extent)
            occupied_lines.append(leader)
            return True
    return False


def _land_fraction_in_bbox(ax, extent, mainland: np.ndarray) -> float:
    sample_columns = np.linspace(extent.x0, extent.x1, num=7)
    sample_rows = np.linspace(extent.y0, extent.y1, num=5)
    columns = ((sample_columns - ax.bbox.x0) / ax.bbox.width * mainland.shape[1]).astype(int)
    rows = ((sample_rows - ax.bbox.y0) / ax.bbox.height * mainland.shape[0]).astype(int)
    valid_columns = (columns >= 0) & (columns < mainland.shape[1])
    valid_rows = (rows >= 0) & (rows < mainland.shape[0])
    if not np.any(valid_columns) or not np.any(valid_rows):
        return 0.0
    sampled_land = mainland[np.ix_(rows[valid_rows], columns[valid_columns])]
    return float(np.count_nonzero(sampled_land)) / sampled_land.size


def _preferred_label_angle(position, marker_positions, axes_bbox) -> float:
    """Point callouts away from nearby vessel clusters, or outward from map center."""
    x, y = position
    direction_x = 0.0
    direction_y = 0.0
    for other_x, other_y in marker_positions:
        delta_x = x - other_x
        delta_y = y - other_y
        distance = math.hypot(delta_x, delta_y)
        if 0 < distance < 180:
            weight = 1 / distance
            direction_x += delta_x * weight
            direction_y += delta_y * weight

    if math.hypot(direction_x, direction_y) < 1e-6:
        direction_x = x - (axes_bbox.x0 + axes_bbox.x1) / 2
        direction_y = y - (axes_bbox.y0 + axes_bbox.y1) / 2
    return math.degrees(math.atan2(direction_y, direction_x))


def _outer_tier_label_indices(marker_positions, crowding) -> Set[int]:
    """Reserve a small share of labels in dense clusters for the farther tier."""
    remaining = set(range(len(marker_positions)))
    outer_tier_indices = set()
    while remaining:
        component = []
        frontier = [remaining.pop()]
        while frontier:
            index = frontier.pop()
            component.append(index)
            neighbors = [
                other_index
                for other_index in remaining
                if math.dist(marker_positions[index], marker_positions[other_index]) < 100
            ]
            for other_index in neighbors:
                remaining.remove(other_index)
                frontier.append(other_index)
        if len(component) >= 4:
            count = max(1, math.ceil(len(component) / 3))
            component.sort(key=lambda index: crowding[index], reverse=True)
            outer_tier_indices.update(component[:count])
    return outer_tier_indices


def _segment_intersects_bbox(start, end, bbox) -> bool:
    """Return whether a line segment touches or crosses a bounding box."""
    x0, y0 = start
    x1, y1 = end
    dx, dy = x1 - x0, y1 - y0
    lower, upper = 0.0, 1.0
    for p, q in (
        (-dx, x0 - bbox.x0),
        (dx, bbox.x1 - x0),
        (-dy, y0 - bbox.y0),
        (dy, bbox.y1 - y0),
    ):
        if p == 0:
            if q < 0:
                return False
            continue
        ratio = q / p
        if p < 0:
            lower = max(lower, ratio)
        else:
            upper = min(upper, ratio)
        if lower > upper:
            return False
    return True


def _segments_intersect(start_a, end_a, start_b, end_b) -> bool:
    def orientation(a, b, c) -> float:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    first = orientation(start_a, end_a, start_b)
    second = orientation(start_a, end_a, end_b)
    third = orientation(start_b, end_b, start_a)
    fourth = orientation(start_b, end_b, end_a)
    if first * second < 0 and third * fourth < 0:
        return True

    def on_segment(a, b, point) -> bool:
        return (
            min(a[0], b[0]) <= point[0] <= max(a[0], b[0])
            and min(a[1], b[1]) <= point[1] <= max(a[1], b[1])
        )

    return (
        (first == 0 and on_segment(start_a, end_a, start_b))
        or (second == 0 and on_segment(start_a, end_a, end_b))
        or (third == 0 and on_segment(start_b, end_b, start_a))
        or (fourth == 0 and on_segment(start_b, end_b, end_a))
    )


def _largest_land_component(land: np.ndarray) -> np.ndarray:
    """Keep only the largest connected land area, excluding islands from label preference."""
    visited = np.zeros_like(land, dtype=bool)
    largest_component = np.zeros_like(land, dtype=bool)
    height, width = land.shape
    largest_size = 0

    for row, column in zip(*np.nonzero(land & ~visited)):
        if visited[row, column]:
            continue
        component = []
        stack = [(int(row), int(column))]
        visited[row, column] = True
        while stack:
            current_row, current_column = stack.pop()
            component.append((current_row, current_column))
            for next_row, next_column in (
                (current_row - 1, current_column),
                (current_row + 1, current_column),
                (current_row, current_column - 1),
                (current_row, current_column + 1),
            ):
                if (
                    0 <= next_row < height
                    and 0 <= next_column < width
                    and land[next_row, next_column]
                    and not visited[next_row, next_column]
                ):
                    visited[next_row, next_column] = True
                    stack.append((next_row, next_column))
        if len(component) > largest_size:
            largest_size = len(component)
            largest_component.fill(False)
            rows, columns = zip(*component)
            largest_component[rows, columns] = True
    return largest_component


def _land_shading(coastline: Coastline, bbox: BBox, width: int, height: int) -> np.ndarray:
    """Rasterize OSM's land-left coastline convention into light-grey land and white water."""
    segments = [
        (x1, y1, x2, y2)
        for line in coastline
        for (x1, y1), (x2, y2) in zip(line, line[1:])
        if y1 != y2
    ]
    longitudes = bbox.min_lon + (np.arange(width) + 0.5) * (
        (bbox.max_lon - bbox.min_lon) / width
    )
    latitudes = bbox.min_lat + (np.arange(height) + 0.5) * (
        (bbox.max_lat - bbox.min_lat) / height
    )
    land = np.zeros((height, width), dtype=bool)
    known_rows = []

    for row, latitude in enumerate(latitudes):
        crossings = []
        for x1, y1, x2, y2 in segments:
            if min(y1, y2) <= latitude < max(y1, y2):
                longitude = x1 + (latitude - y1) * (x2 - x1) / (y2 - y1)
                # OSM coastline ways keep land on their left: a southbound
                # crossing changes from water to land when moving west to east.
                land_after = y2 < y1
                crossings.append((longitude, land_after))
        if not crossings:
            continue

        known_rows.append(row)
        crossings.sort(key=lambda crossing: crossing[0])
        is_land = not crossings[0][1]
        previous_lon = bbox.min_lon
        for longitude, land_after in crossings:
            end = int(np.searchsorted(longitudes, longitude, side="left"))
            if is_land:
                start = int(np.searchsorted(longitudes, previous_lon, side="left"))
                land[row, start:end] = True
            is_land = land_after
            previous_lon = longitude
        if is_land:
            start = int(np.searchsorted(longitudes, previous_lon, side="left"))
            land[row, start:] = True

    if known_rows:
        known_row_set = set(known_rows)
        for row in range(height):
            if row not in known_row_set:
                nearest = min(known_rows, key=lambda known: abs(known - row))
                land[row] = land[nearest]

    image = np.full((height, width), 255, dtype=np.uint8)
    image[land] = 230
    return image


def _redraw_markers(ax, visible, settings: Settings) -> None:
    global _marker_artists
    for artist in _marker_artists:
        artist.remove()
    _marker_artists = []
    for x, y, vessel in visible:
        if vessel.sog is not None and 0 <= vessel.sog <= STATIC_SPEED_THRESHOLD_KNOTS:
            ring = ax.scatter(
                [x],
                [y],
                s=72,
                facecolors="white",
                edgecolors="black",
                linewidths=0.8,
                marker="o",
                zorder=3,
            )
            dot = ax.scatter(
                [x],
                [y],
                s=9,
                c="black",
                marker="o",
                linewidths=0,
                zorder=4,
            )
            _marker_artists.extend((ring, dot))
            continue
        marker = MarkerStyle(_SHIP_MARKER)
        marker._transform = Affine2D().rotate_deg(-vessel.course)
        artist = ax.scatter(
            [x],
            [y],
            s=38,
            c="black",
            marker=marker,
            linewidths=0,
            zorder=3,
        )
        _marker_artists.append(artist)
