from __future__ import annotations
import io, math
from pathlib import Path
import httpx
from PIL import Image, ImageDraw, ImageFont

TILE = 256

def _world(lat, lon, zoom):
    lat = max(min(lat, 85.05112878), -85.05112878)
    n = 2 ** zoom
    x = (lon + 180.0) / 360.0 * n * TILE
    lat_rad = math.radians(lat)
    y = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n * TILE
    return x, y

def _choose_zoom(points, width, height, padding=80):
    for z in range(17, 5, -1):
        xy = [_world(lat, lon, z) for lat, lon in points]
        xs, ys = zip(*xy)
        if max(xs)-min(xs) <= width-2*padding and max(ys)-min(ys) <= height-2*padding:
            return z
    return 6

def render_map(
    output: str | Path,
    depot: tuple[float,float],
    stops_ordered: list[tuple[float,float,str]],
    geometry: list[tuple[float,float]],
    tile_url: str,
    user_agent: str,
    width: int = 1600,
    height: int = 1200,
):
    all_pts = [depot] + [(a,b) for a,b,_ in stops_ordered] + geometry
    zoom = _choose_zoom(all_pts, width, height)
    xy = [_world(lat, lon, zoom) for lat, lon in all_pts]
    xs, ys = zip(*xy)
    cx, cy = (min(xs)+max(xs))/2, (min(ys)+max(ys))/2
    left, top = cx-width/2, cy-height/2

    img = Image.new("RGB", (width, height), "white")
    client = httpx.Client(timeout=15, headers={"User-Agent": user_agent})

    x0, x1 = int(left // TILE), int((left + width) // TILE)
    y0, y1 = int(top // TILE), int((top + height) // TILE)
    n = 2 ** zoom
    for tx in range(x0, x1+1):
        for ty in range(y0, y1+1):
            if not (0 <= ty < n):
                continue
            url = tile_url.format(z=zoom, x=tx % n, y=ty)
            try:
                r = client.get(url)
                r.raise_for_status()
                tile = Image.open(io.BytesIO(r.content)).convert("RGB")
                img.paste(tile, (round(tx*TILE-left), round(ty*TILE-top)))
            except Exception:
                pass

    draw = ImageDraw.Draw(img)

    def px(lat, lon):
        x, y = _world(lat, lon, zoom)
        return (x-left, y-top)

    # Route line
    route_px = [px(lat, lon) for lat, lon in geometry]
    if len(route_px) >= 2:
        draw.line(route_px, fill=(30, 85, 190), width=8, joint="curve")
        # Direction arrows at intervals along the route.
        stride = max(20, len(route_px)//20)
        for i in range(stride, len(route_px)-1, stride):
            x1p,y1p = route_px[i-1]; x2p,y2p = route_px[i+1]
            ang = math.atan2(y2p-y1p, x2p-x1p)
            size = 14
            tip = (route_px[i][0], route_px[i][1])
            p2 = (tip[0]-size*math.cos(ang-0.55), tip[1]-size*math.sin(ang-0.55))
            p3 = (tip[0]-size*math.cos(ang+0.55), tip[1]-size*math.sin(ang+0.55))
            draw.polygon([tip,p2,p3], fill=(30,85,190))

    font = ImageFont.load_default(size=22) if hasattr(ImageFont, "load_default") else None

    # Depot marker.
    dx, dy = px(*depot)
    draw.rounded_rectangle((dx-24,dy-24,dx+24,dy+24), radius=8, fill=(30,30,30), outline="white", width=4)
    draw.text((dx,dy), "S", fill="white", anchor="mm", font=font)

    # Stop marker: pickup green, delivery orange.
    for num, (lat, lon, op) in enumerate(stops_ordered, start=1):
        x,y = px(lat,lon)
        fill = (35,140,80) if op == "pickup" else (220,105,30)
        draw.ellipse((x-23,y-23,x+23,y+23), fill=fill, outline="white", width=4)
        draw.text((x,y), str(num), fill="white", anchor="mm", font=font)

    # Attribution is mandatory for OSM standard tiles.
    attrib = "© OpenStreetMap contributors"
    draw.rectangle((8,height-32,260,height-6), fill=(255,255,255))
    draw.text((12,height-28), attrib, fill="black")

    Path(output).parent.mkdir(parents=True, exist_ok=True)
    img.save(output, "PNG")
