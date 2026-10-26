# vessel-tracker

Python prototype for live AIS vessel positions over an OpenStreetMap coastline. The renderer produces an 800×400 high-contrast image for an e-ink display, suitable for conversion to a 1-bit image.

OpenAIS itself is a self-hosted PostGIS pipeline. This prototype talks to the same *kind* of open AIS feed without standing up Docker:

- **Open Waters AIS** (default): global live WebSocket, no API key for personal use
- **Kystverket** (Norwegian Coast Guard TCP): the feed OpenAIS uses in its quick start. Optional extra: `pip install pyais` (needs a C compiler on this Python 3.8 setup)

## Setup

```powershell
cd "$env:USERPROFILE\vessel-tracker"
& "$env:USERPROFILE\anaconda3\python.exe" -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m vessel_tracker
```

If OpenSSL/numpy DLL errors appear, use the helper (it puts Anaconda's `Library\bin` on PATH):

```powershell
.\run.ps1
.\run.ps1 --demo
.\run.ps1 --save preview.png
```

With the default Open Waters source, `--save` fetches a one-time HTTP snapshot of vessels instead of opening a live stream. The interactive display continues to use the live AIS stream.

Demo mode (no live feed):

```powershell
python -m vessel_tracker --demo
```

## Configure

Edit `config.yaml`:

- `map.width_km` — map width; height is derived from the display aspect ratio
- `map.center_lat` / `map.center_lon` — reference position (default: Vigo)
- `map.anchor_x_fraction` — horizontal position of the reference point within the map (0 = left, 1 = right)
- `display.vessel_types` — vessel categories to show (for example, `[cargo]`); use `[]` to show all
- Moving vessels use a small, heading-oriented ship silhouette with a pointed bow, straight sides, and flat stern; vessels at or below 0.5 knots use a small dot inside an outlined circle
- Vessel callouts show the vessel name in bold, followed by type, speed, and destination; recognized AIS port codes are expanded to city names, with unrecognized destinations shown as received
- Callouts use two distance tiers: labels in dense clusters are automatically split between the closer tier and a farther tier, with most labels kept closer. Placement avoids overlaps and strongly prefers label boxes that sit over the largest mainland land area rather than islands or water.
- `ais.source` — `openwaters` or `kystverket`
- `display.eink` — grayscale, high-contrast basemap with light-grey land shading
OSM coastline ways (`natural=coastline`) are cached under `.cache/coastline` and used to render the land shading. The current map extent's coastline snapshot is bundled under `assets/coastline` so scheduled renders can still work when Overpass is unavailable. Attribution: © OpenStreetMap contributors.

## Publish a live-updating webpage

The GitHub Actions workflow in `.github/workflows/publish-vessel-map.yml` renders a fresh map every 15 minutes and deploys it to GitHub Pages. The page at `site/index.html` displays the latest image and update time, and refreshes the image in the browser once per minute.

To publish it:

1. Push this project to a GitHub repository. Do not commit `.venv`, `.cache`, or credentials.
2. In the repository settings, open **Pages** and set the build and deployment source to **GitHub Actions**.
3. Open the **Actions** tab and run **Publish vessel map** once with **Run workflow**.
4. Open **Settings → Pages** to find the published webpage URL.

The workflow caches coastline data between runs and requests the public Open Waters AIS snapshot; no API secrets are configured or required. GitHub scheduled workflows are approximate and may start later than their scheduled time.
