"""
Build all map layers for "Where Can Nature Survive Climate Change?"

Usage:
    bash scripts/fetch_data.sh raw
    python scripts/prep_richness.py raw
    python scripts/build_data.py raw data

Outputs (in <out>):
    layers/*.png         Web-Mercator RGBA overlays (lat -60…85)
    grid.json            0.5° cell values for click read-outs
    stats.json           headline numbers, legends, hotspot table
    hotspots.geojson     simplified CI biodiversity hotspots
    land.geojson         simplified land / country outlines for the basemap
"""
import base64
import glob
import json
import math
import os
import sys

import numpy as np
import pandas as pd

import xarray as xr
from PIL import Image, ImageDraw
from scipy import ndimage
from shapely.geometry import mapping, shape

RAW = sys.argv[1] if len(sys.argv) > 1 else "raw"
OUT = sys.argv[2] if len(sys.argv) > 2 else "data"
os.makedirs(f"{OUT}/layers", exist_ok=True)

SCEN = ["ssp126", "ssp245", "ssp585"]
BASE = (1995, 2014)          # IPCC AR6 recent-past baseline
FUT = (2041, 2060)           # "2050" horizon
YEARS = 2050.5 - 2005.0      # years between period mid-points
LAPSE = 6.5 / 1000.0         # °C per metre
RES = 6                      # cells per degree on the fine grid (10 arc-min)
NX, NY = 360 * RES, 180 * RES
LAT_MIN, LAT_MAX = -60, 85   # displayed extent

lat_c = 90 - (np.arange(NY) + 0.5) / RES            # fine grid centres, N→S
lon_c = -180 + (np.arange(NX) + 0.5) / RES


def log(*a):
    print("·", *a, flush=True)


# ---------------------------------------------------------------- rasteriser
def rasterize(geoms, nx=NX, ny=NY, ss=2):
    """Rasterise (multi)polygons to a fraction-covered grid (global lon/lat)."""
    W, H = nx * ss, ny * ss
    img = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(img)
    sx, sy = W / 360.0, H / 180.0

    def ring(coords):
        return [((x + 180) * sx, (90 - y) * sy) for x, y in coords]

    for g in geoms:
        polys = [g] if g.geom_type == "Polygon" else list(g.geoms)
        for p in polys:
            d.polygon(ring(p.exterior.coords), fill=255)
            for hole in p.interiors:
                d.polygon(ring(hole.coords), fill=0)
    a = np.asarray(img, dtype=np.float32) / 255.0
    return a.reshape(ny, ss, nx, ss).mean(axis=(1, 3))


def label_raster(geoms_by_id, nx=NX, ny=NY):
    out = np.full((ny, nx), -1, dtype=np.int16)
    for i, g in geoms_by_id:
        m = rasterize([g], nx, ny, ss=1) > 0.5
        out[m] = i
    return out


# ---------------------------------------------------------------- 1. terrain
log("ETOPO5 relief")
z5 = np.fromfile(f"{RAW}/geocat/binary_files/ETOPO5.DAT", dtype=">i2").reshape(2160, 4320)
z5 = np.roll(z5.astype(np.float32), 2160, axis=1)          # 0…360 → -180…180
z5l = np.maximum(z5, 0)
z10 = z5l.reshape(NY, 2, NX, 2).mean(axis=(1, 3))          # 10′ mean elevation
# local relief: elevation range in a ~30′ (≈55 km) moving window, at 5′ then 10′
zmax = ndimage.maximum_filter(z5l, size=6, mode="wrap")
zmin = ndimage.minimum_filter(z5l, size=6, mode="wrap")
relief = (zmax - zmin).reshape(NY, 2, NX, 2).mean(axis=(1, 3))

log("Land mask")
land_geo = json.load(open(f"{RAW}/ne/ne_50m_land.geojson"))
land_geoms = [shape(f["geometry"]) for f in land_geo["features"]]
landfrac = rasterize(land_geoms, ss=3)
ice_geo = json.load(open(f"{RAW}/ne/ne_50m_glaciated_areas.geojson"))
icefrac = rasterize([shape(f["geometry"]).buffer(0) for f in ice_geo["features"]], ss=3)
# permanent ice (e.g. Greenland ice sheet) is excluded: not habitat in the sense of this map
land = (landfrac >= 0.4) & (icefrac < 0.5) & (lat_c[:, None] >= LAT_MIN) & (lat_c[:, None] <= LAT_MAX)

# ---------------------------------------------------------------- 2. baseline climate
log("ERA-Interim T2M → downscaled baseline temperature")
era = xr.open_dataset(f"{RAW}/geocat/netcdf_files/T2M_ERAINT_rectilinear_grid_2D.nc", decode_times=False)
T = era["T2M"].mean("time").values.astype(np.float64)
if np.nanmean(T) > 100:
    T -= 273.15
elat, elon = era["lat"].values, era["lon"].values
if elat[0] < elat[-1]:
    T, elat = T[::-1], elat[::-1]
# put on -180…180
shift = np.searchsorted(elon, 180.0)
T = np.roll(T, -shift, axis=1)
elon = np.roll(np.where(elon >= 180, elon - 360, elon), -shift)
# ERA orography proxy: ETOPO smoothed to the ERA grid scale (~0.7°)
zs = ndimage.gaussian_filter(z5l, sigma=(4.2, 4.2), mode="wrap")
from scipy.interpolate import RegularGridInterpolator as RGI

# wrap longitudes for interpolation
elon_w = np.concatenate([elon[-1:] - 360, elon, elon[:1] + 360])
T_w = np.concatenate([T[:, -1:], T, T[:, :1]], axis=1)
interp = RGI((elat[::-1], elon_w), T_w[::-1], bounds_error=False, fill_value=None)
LL = np.stack(np.meshgrid(lat_c, lon_c, indexing="ij"), -1)
T_era10 = interp(LL.reshape(-1, 2)).reshape(NY, NX)
zs10 = zs.reshape(NY, 2, NX, 2).mean(axis=(1, 3))
Tb = T_era10 + LAPSE * (zs10 - z10)                         # lapse-rate downscaling

# spatial gradient (°C/km), 3×3 neighbourhood as in Loarie et al. (2009)
dy_km = 111.32 / RES
dx_km = dy_km * np.cos(np.deg2rad(lat_c))[:, None]
gy = (np.roll(Tb, 1, 0) - np.roll(Tb, -1, 0)) / (2 * dy_km)
gx = (np.roll(Tb, -1, 1) - np.roll(Tb, 1, 1)) / (2 * dx_km)
gy = ndimage.uniform_filter(gy, 3, mode="wrap")
gx = ndimage.uniform_filter(gx, 3, mode="wrap")
grad = np.sqrt(gx ** 2 + gy ** 2)
grad = np.maximum(grad, 1e-4)                               # avoid ∞ on perfectly flat cells

# ---------------------------------------------------------------- 3. CMIP6 regional warming
log("IPCC Atlas CMIP6 regional ensemble")
reg_geo = json.load(open(f"{RAW}/atlas/reference-regions/IPCC-WGI-reference-regions-v4.geojson"))
land_regs = [f for f in reg_geo["features"] if f["properties"]["Type"] in ("Land", "Land-Ocean")]
acr = [f["properties"]["Acronym"] for f in land_regs]
names = {f["properties"]["Acronym"]: f["properties"]["Name"] for f in land_regs}

csvdir = f"{RAW}/atlas/datasets-aggregated-regionally/data/CMIP6/CMIP6_tas_land"


def annual(path):
    df = pd.read_csv(path, comment="#")
    df["year"] = df["date"].str[:4].astype(int)
    return df.drop(columns="date").groupby("year").mean()


hist = {}
for p in glob.glob(f"{csvdir}/CMIP6_*_historical_*.csv"):
    m = os.path.basename(p).split("_historical_")[0][6:]
    hist[m] = annual(p)

reg_dT = {s: {} for s in SCEN}       # region -> list over models
reg_sig = {}
glob_dT = {s: [] for s in SCEN}
nmod = {}
for s in SCEN:
    files = glob.glob(f"{csvdir}/CMIP6_*_{s}_*.csv")
    used = 0
    for p in files:
        m = os.path.basename(p).split(f"_{s}_")[0][6:]
        if m not in hist:
            continue
        fut = annual(p)
        h = hist[m]
        b = h.loc[BASE[0]:BASE[1]].mean()
        f_ = fut.loc[FUT[0]:FUT[1]].mean()
        d = f_ - b
        used += 1
        glob_dT[s].append(d["world"])
        for a in acr:
            reg_dT[s].setdefault(a, []).append(d[a])
    nmod[s] = used
for m, h in hist.items():
    seg = h.loc[1965:2014]
    for a in acr:
        y = seg[a].values
        t = np.arange(len(y))
        if np.isnan(y).any():
            continue
        res = y - np.polyval(np.polyfit(t, y, 2), t)
        reg_sig.setdefault(a, []).append(res.std(ddof=1))

reg_table = {}
for a in acr:
    reg_table[a] = {
        "name": names[a],
        "sigma": float(np.median(reg_sig[a])),
        **{f"dT_{s}": float(np.nanmedian(reg_dT[s][a])) for s in SCEN},
        **{f"dT_{s}_p10": float(np.nanpercentile(reg_dT[s][a], 10)) for s in SCEN},
        **{f"dT_{s}_p90": float(np.nanpercentile(reg_dT[s][a], 90)) for s in SCEN},
    }
log("models used:", nmod, "global ΔT:", {s: round(float(np.median(v)), 2) for s, v in glob_dT.items()})

# rasterise regions; fill gaps by nearest region; smooth the step at borders
reg_id = label_raster([(i, shape(f["geometry"])) for i, f in enumerate(land_regs)])
missing = reg_id < 0
_, (iy, ix) = ndimage.distance_transform_edt(missing, return_indices=True)
reg_id_f = reg_id[iy, ix]


def region_field(key):
    vals = np.array([reg_table[a][key] for a in acr])
    f = vals[reg_id_f]
    return ndimage.gaussian_filter(f, sigma=(3 * RES, 3 * RES), mode="wrap")


SIG = region_field("sigma")
DT = {s: region_field(f"dT_{s}") for s in SCEN}

# ---------------------------------------------------------------- 4. refugia metrics
log("Velocity, novelty, refugia index")
VEL = {s: (DT[s] / YEARS) / grad for s in SCEN}                 # km / yr
SNR = {s: DT[s] / SIG for s in SCEN}                            # signal-to-noise
lrel = np.log1p(relief)

L = land
ref_logv = np.sort(np.log10(VEL["ssp245"][L]))
ref_snr = np.sort(SNR["ssp245"][L])
ref_rel = np.sort(lrel[L])


def cdf(sorted_ref, x):
    return np.interp(x, sorted_ref, np.linspace(0, 1, len(sorted_ref)))


S_rel = cdf(ref_rel, lrel)
RIDX = {}
for s in SCEN:
    s_v = 1 - cdf(ref_logv, np.log10(VEL[s]))
    s_n = 1 - cdf(ref_snr, SNR[s])
    RIDX[s] = 0.5 * s_v + 0.3 * s_n + 0.2 * S_rel
# class breaks = SSP2-4.5 quintiles (fixed for every scenario)
qbreaks = np.quantile(RIDX["ssp245"][L], [0.2, 0.4, 0.6, 0.8]).tolist()
CLASS_NAMES = ["Highly exposed", "Exposed", "Transitional", "Moderate refugium", "Strong refugium"]

# ---------------------------------------------------------------- 5. biodiversity (0.5°)
log("Species richness (IUCN / BirdLife / GARD)")
CX, CY = 720, 360
clat = 90 - (np.arange(CY) + 0.5) / 2
clon = -180 + (np.arange(CX) + 0.5) / 2


RZ = np.load(f"{RAW}/richness.npz")   # produced by prep_richness.py


def richness(name):
    return RZ[name].astype(np.int32), int(RZ[name + "_n"])


groups = {"amphibians": "amphibians_dist", "birds": "ter_birds_dist",
          "mammals": "ter_mammals_dist", "reptiles": "gard_reptiles_dist"}
RICH, NSP = {}, {}
for k, f in groups.items():
    RICH[k], NSP[k] = richness(f)
    log(k, NSP[k], "species, max", RICH[k].max())
THR = np.zeros((CY, CX), np.int32)
nthr = 0
for f in ["amphibians_threatened", "ter_birds_threatened", "ter_mammals_threatened"]:
    g, n = richness(f)
    THR += g
    nthr += n
TOT = sum(RICH.values())

# aggregate fine grid to 0.5°
def agg(a, fn=np.nanmean):
    b = np.where(L, a, np.nan).reshape(CY, 3, CX, 3)
    with np.errstate(all="ignore"):
        return fn(b, axis=(1, 3))


Lc = agg(L.astype(float), np.nanmax) > 0
Lc &= (clat[:, None] >= LAT_MIN) & (clat[:, None] <= LAT_MAX)
RIDXc = {s: agg(RIDX[s]) for s in SCEN}
DTc = {s: agg(DT[s]) for s in SCEN}
VELc = {s: 10 ** agg(np.log10(VEL[s])) for s in SCEN}
RELc = agg(relief)
REGc = reg_id_f.reshape(CY, 3, CX, 3)[:, 1, :, 1]

rich_mask = Lc & (TOT > 0)
rich_t = np.quantile(TOT[rich_mask], [1 / 3, 2 / 3]).tolist()
ref_t = np.quantile(RIDXc["ssp245"][rich_mask], [1 / 3, 2 / 3]).tolist()


def tercile(x, br):
    return np.digitize(x, br)  # 0,1,2


BIV = {}
for s in SCEN:
    b = tercile(TOT, rich_t) * 3 + tercile(RIDXc[s], ref_t)  # richness row, refugia col
    BIV[s] = np.where(rich_mask, b, -1)

# ---------------------------------------------------------------- 6. hotspots
log("Biodiversity hotspots")
hs_feats, hs_geoms = [], []
for p in sorted(glob.glob(f"{RAW}/hotspots/*_hotspot_area.geojson")):
    f = json.load(open(p))
    g = shape(f["geometry"]).buffer(0)
    hs_geoms.append((len(hs_geoms), g))
    gs = g.simplify(0.03, preserve_topology=True)
    rp = max(list(g.geoms) if g.geom_type == "MultiPolygon" else [g], key=lambda x: x.area).representative_point()
    hs_feats.append({"type": "Feature", "properties": {"id": len(hs_feats), "name": f["properties"]["NAME"],
                                                       "pt": [round(rp.y, 3), round(rp.x, 3)]},
                     "geometry": json.loads(json.dumps(mapping(gs)), parse_float=lambda v: round(float(v), 3))})
HS = label_raster(hs_geoms)
HSc = HS.reshape(CY, 3, CX, 3)[:, 1, :, 1]

hs_stats = []
for i, g in hs_geoms:
    m = (HS == i) & L
    if m.sum() == 0:  # tiny islands: fall back to centroid neighbourhood
        c = g.representative_point()
        r, cc = int((90 - c.y) * RES), int((c.x + 180) * RES)
        m = np.zeros_like(L)
        m[max(r - 2, 0):r + 3, max(cc - 2, 0):cc + 3] = True
        m &= L
    mc = (HSc == i) & rich_mask
    row = {"id": i, "name": hs_feats[i]["properties"]["name"], "cells": int(m.sum()),
           "richness": int(np.round(np.nanmean(TOT[mc]))) if mc.any() else None,
           "threatened": int(np.round(np.nanmean(THR[mc]))) if mc.any() else None,
           "relief_m": int(np.nanmean(relief[m])) if m.any() else None}
    for s in SCEN:
        r = RIDX[s][m]
        row[f"dT_{s}"] = round(float(np.nanmean(DT[s][m])), 2) if m.any() else None
        row[f"vel_{s}"] = round(float(10 ** np.nanmean(np.log10(VEL[s][m]))), 3) if m.any() else None
        row[f"refuge_{s}"] = round(float((r >= qbreaks[2]).mean() * 100), 1) if m.any() else None
        row[f"exposed_{s}"] = round(float((r < qbreaks[1]).mean() * 100), 1) if m.any() else None
        row[f"index_{s}"] = round(float(r.mean()), 3) if m.any() else None
    hs_stats.append(row)

# ---------------------------------------------------------------- 7. rendering
log("Rendering Web-Mercator PNGs")


def merc(lat):
    return np.log(np.tan(np.pi / 4 + np.deg2rad(lat) / 2))


W = NX
y0, y1 = merc(LAT_MAX), merc(LAT_MIN)
H = int(round((y0 - y1) / (2 * np.pi) * W))
ys = y0 - (np.arange(H) + 0.5) / H * (y0 - y1)
lat_rows = np.rad2deg(2 * np.arctan(np.exp(ys)) - np.pi / 2)
src_rows = np.clip(((90 - lat_rows) * RES).astype(int), 0, NY - 1)


def hex2rgb(h):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], float)


def ramp(stops):
    """stops: list of (t, hex) → function mapping [0,1] array to RGB"""
    ts = np.array([t for t, _ in stops])
    cs = np.array([hex2rgb(c) for _, c in stops])

    def f(x):
        x = np.clip(x, 0, 1)
        return np.stack([np.interp(x, ts, cs[:, k]) for k in range(3)], -1)
    return f


def save_png(rgb, mask, name, alpha=235):
    rgba = np.zeros(rgb.shape[:2] + (4,), np.uint8)
    rgba[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    rgba[..., 3] = np.where(mask, alpha, 0)
    rgba = rgba[src_rows]
    im = Image.fromarray(rgba, "RGBA")
    im = im.quantize(colors=128, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
    im.save(f"{OUT}/layers/{name}.png", optimize=True)


def up(a):  # 0.5° → 10′ nearest
    return np.repeat(np.repeat(a, 3, axis=0), 3, axis=1)


# palettes (dark basemap → low values dark, high values bright)
P_INDEX = [(0.00, "#b2182b"), (0.25, "#e0714f"), (0.50, "#5b5f66"), (0.75, "#3aa7a0"), (1.00, "#9ff0d8")]
P_DT = [(0.0, "#2b2a33"), (0.25, "#6b2f45"), (0.5, "#b3403f"), (0.75, "#e8823a"), (1.0, "#ffe08a")]
P_VEL = [(0.0, "#1f2a44"), (0.33, "#3b4f8f"), (0.66, "#8a6cc9"), (1.0, "#f3c6ff")]
P_REL = [(0.0, "#22272e"), (0.4, "#5a5f55"), (0.75, "#b6ab84"), (1.0, "#fff3cf")]
P_RICH = [(0.0, "#1d2b2a"), (0.35, "#1f6150"), (0.7, "#43b27f"), (1.0, "#e9ffb5")]
P_THR = [(0.0, "#2a2230"), (0.35, "#5d2f63"), (0.7, "#b04c8f"), (1.0, "#ffc4e6")]

f_index = ramp(P_INDEX)
DT_RANGE = (0.5, 3.5)
VEL_RANGE = (-1.5, 1.5)        # log10 km/yr
for s in SCEN:
    # index: stretch so class breaks are visually even
    t = np.interp(RIDX[s], [0] + qbreaks + [1], [0, .2, .4, .6, .8, 1])
    save_png(f_index(t), L, f"m1_index_{s}")
    save_png(ramp(P_DT)((DT[s] - DT_RANGE[0]) / (DT_RANGE[1] - DT_RANGE[0])), L, f"m1_dt_{s}")
    save_png(ramp(P_VEL)((np.log10(VEL[s]) - VEL_RANGE[0]) / (VEL_RANGE[1] - VEL_RANGE[0])), L, f"m1_vel_{s}")
REL_MAX = 3000
save_png(ramp(P_REL)(np.log1p(relief) / np.log1p(REL_MAX)), L, "m1_relief")

# bivariate palette: richness → magenta, refugia → teal, both → pale cyan-white
base, M, Tq = hex2rgb("#2c3140"), hex2rgb("#d4509a"), hex2rgb("#2bb5a5")
BIV_COLORS = []
for i in range(3):
    for j in range(3):
        c = base + (i / 2) * (M - base) + (j / 2) * (Tq - base)
        if i == 2 and j == 2:
            c = hex2rgb("#e9fbff")
        BIV_COLORS.append("#%02x%02x%02x" % tuple(int(v) for v in np.clip(c, 0, 255)))
biv_rgb = np.array([hex2rgb(c) for c in BIV_COLORS])
Lr = up(rich_mask)
for s in SCEN:
    b = up(BIV[s])
    save_png(biv_rgb[np.clip(b, 0, 8)], Lr & (b >= 0), f"m2_biv_{s}", alpha=245)
RICH_MAX = float(np.quantile(TOT[rich_mask], 0.995))
THR_MAX = float(np.quantile(THR[rich_mask], 0.995))
save_png(ramp(P_RICH)(up(TOT) / RICH_MAX), Lr, "m2_rich")
save_png(ramp(P_THR)(np.sqrt(up(THR) / THR_MAX)), Lr & (up(THR) > 0), "m2_thr")

# ---------------------------------------------------------------- 8. read-out grid (0.5°)
log("Read-out grid")
rows = np.where((clat >= LAT_MIN) & (clat <= LAT_MAX))[0]
r0, r1 = rows[0], rows[-1] + 1


def pack(a, dtype):
    a = a[r0:r1]
    return base64.b64encode(np.ascontiguousarray(a.astype(dtype)).tobytes()).decode()


NA8, NA16 = 255, 65535
grid = {"res": 0.5, "lat0": float(90 - r0 * 0.5), "lon0": -180.0, "nx": CX, "ny": int(r1 - r0),
        "regions": [{"acr": a, "name": names[a]} for a in acr], "hotspots": [h["properties"]["name"] for h in hs_feats],
        "fields": {}}


def u8(a, scale, offset=0.0, mask=Lc):
    v = np.round((np.nan_to_num(a, nan=-1e9) - offset) * scale)
    v = np.where(mask & np.isfinite(a), np.clip(v, 0, 254), NA8)
    return v


def u16(a, mask):
    return np.where(mask, np.clip(a, 0, 65534), NA16)


F = grid["fields"]
for s in SCEN:
    F[f"index_{s}"] = {"t": "u8", "scale": 250, "off": 0, "d": pack(u8(RIDXc[s], 250), np.uint8)}
    F[f"dt_{s}"] = {"t": "u8", "scale": 50, "off": 0, "d": pack(u8(DTc[s], 50), np.uint8)}
    F[f"lvel_{s}"] = {"t": "u8", "scale": 40, "off": -3, "d": pack(u8(np.log10(VELc[s]), 40, -3), np.uint8)}
    F[f"biv_{s}"] = {"t": "u8", "scale": 1, "off": 0, "d": pack(np.where(BIV[s] >= 0, BIV[s], NA8), np.uint8)}
F["relief"] = {"t": "u16", "scale": 1, "off": 0, "d": pack(u16(np.nan_to_num(RELc), Lc), np.uint16)}
for k in groups:
    F[f"rich_{k}"] = {"t": "u16", "scale": 1, "off": 0, "d": pack(u16(RICH[k], Lc), np.uint16)}
F["thr"] = {"t": "u16", "scale": 1, "off": 0, "d": pack(u16(THR, Lc), np.uint16)}
F["region"] = {"t": "u8", "scale": 1, "off": 0, "d": pack(np.where(Lc, REGc, NA8), np.uint8)}
F["hotspot"] = {"t": "u8", "scale": 1, "off": 0, "d": pack(np.where(HSc >= 0, HSc, NA8), np.uint8)}
json.dump(grid, open(f"{OUT}/grid.json", "w"), separators=(",", ":"))

# ---------------------------------------------------------------- 9. headline stats
log("Stats")
area = np.cos(np.deg2rad(lat_c))[:, None] * np.ones((1, NX))
areac = np.cos(np.deg2rad(clat))[:, None] * np.ones((1, CX))


def share(mask, within):
    return round(float(area[mask & within].sum() / area[within].sum() * 100), 1)


def sharec(mask, within):
    return round(float(areac[mask & within].sum() / areac[within].sum() * 100), 1)


headline = {}
for s in SCEN:
    r = RIDX[s]
    headline[s] = {
        "global_dT": round(float(np.median(glob_dT[s])), 2),
        "land_dT": round(float((DT[s] * area)[L].sum() / area[L].sum()), 2),
        "models": nmod[s],
        "strong_refugia_pct": share(r >= qbreaks[3], L),
        "refugia_pct": share(r >= qbreaks[2], L),
        "exposed_pct": share(r < qbreaks[1], L),
        "median_velocity": round(float(np.median(VEL[s][L])), 3),
        "safe_haven_pct": sharec(BIV[s] == 8, rich_mask),
        "at_risk_pct": sharec(BIV[s] == 6, rich_mask),
        # share of the richest third of land that is also in top-third refugia
        "rich_in_refuge_pct": sharec(BIV[s] == 8, rich_mask & (tercile(TOT, rich_t) == 2)),
        "rich_exposed_pct": sharec(BIV[s] == 6, rich_mask & (tercile(TOT, rich_t) == 2)),
    }
    for k in ("safe_haven_pct", "at_risk_pct"):
        pass

# continents for the "where" sentence: top refugia regions by share
reg_rank = {}
for s in SCEN:
    rr = []
    for i, a in enumerate(acr):
        m = (reg_id == i) & L
        if area[m].sum() < 50:
            continue
        rr.append({"acr": a, "name": names[a], "refuge_pct": round(float(area[m & (RIDX[s] >= qbreaks[2])].sum() / area[m].sum() * 100), 1),
                   "dT": round(reg_table[a][f"dT_{s}"], 2)})
    reg_rank[s] = sorted(rr, key=lambda x: -x["refuge_pct"])

stats = {
    "scenarios": SCEN,
    "headline": headline,
    "regions": reg_rank,
    "region_table": reg_table,
    "class_breaks": qbreaks,
    "class_names": CLASS_NAMES,
    "rich_terciles": rich_t,
    "refuge_terciles": ref_t,
    "species_counts": {k: int(v) for k, v in NSP.items()},
    "threatened_species": int(nthr),
    "biv_colors": BIV_COLORS,
    "palettes": {"index": P_INDEX, "dt": P_DT, "vel": P_VEL, "relief": P_REL, "rich": P_RICH, "thr": P_THR},
    "ranges": {"dt": DT_RANGE, "lvel": VEL_RANGE, "relief_max": REL_MAX, "rich_max": RICH_MAX, "thr_max": THR_MAX},
    "hotspots": hs_stats,
    "bounds": [[LAT_MIN, -180], [LAT_MAX, 180]],
}
json.dump(stats, open(f"{OUT}/stats.json", "w"), indent=1)
json.dump({"type": "FeatureCollection", "features": hs_feats}, open(f"{OUT}/hotspots.geojson", "w"), separators=(",", ":"))

# basemap: countries (110m) rounded
cty = json.load(open(f"{RAW}/ne/ne_110m_admin_0_countries.geojson"))
feats = []
for f in cty["features"]:
    g = shape(f["geometry"]).simplify(0.05, preserve_topology=True)
    feats.append({"type": "Feature", "properties": {"n": f["properties"]["NAME"]},
                  "geometry": json.loads(json.dumps(mapping(g)), parse_float=lambda v: round(float(v), 2))})
json.dump({"type": "FeatureCollection", "features": feats}, open(f"{OUT}/countries.geojson", "w"), separators=(",", ":"))
# map labels (Natural Earth, public domain) — replaces any third-party label tiles
labels = []
for f in json.load(open(f"{RAW}/ne/ne_50m_admin_0_countries.geojson"))["features"]:
    pr = f["properties"]
    if pr.get("LABEL_Y") is None or pr["LABEL_Y"] < LAT_MIN:
        continue
    labels.append(["c", pr["NAME"], round(pr["LABEL_Y"], 2), round(pr["LABEL_X"], 2), round(float(pr.get("MIN_LABEL") or 5), 1)])
for f in json.load(open(f"{RAW}/ne/ne_50m_populated_places.geojson"))["features"]:
    pr = f["properties"]
    if pr["MIN_ZOOM"] > 6 or pr["LATITUDE"] < LAT_MIN:
        continue
    labels.append(["p", pr["NAME"], round(pr["LATITUDE"], 2), round(pr["LONGITUDE"], 2), round(float(pr["MIN_ZOOM"]), 1)])
json.dump(labels, open(f"{OUT}/labels.json", "w"), separators=(",", ":"), ensure_ascii=False)
log("done →", OUT)
for s in SCEN:
    log(s, headline[s])
