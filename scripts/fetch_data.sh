#!/usr/bin/env bash
# Downloads every raw input used by build_data.py into ./raw (≈ 60 MB).
# All sources are public GitHub repositories, so the pipeline is fully reproducible.
set -euo pipefail
RAW="${1:-raw}"
mkdir -p "$RAW"
cd "$RAW"

sparse() { # repo dir paths...
  local repo=$1 dir=$2; shift 2
  if [ ! -d "$dir" ]; then
    git clone -q --depth 1 --filter=blob:none --no-checkout "https://github.com/$repo.git" "$dir"
  fi
  (cd "$dir" && git checkout -q HEAD -- "$@")
}

# 1. IPCC AR6 WGI Atlas — CMIP6 regional (land) monthly tas + reference regions
sparse IPCC-WG1/Atlas atlas \
  datasets-aggregated-regionally/data/CMIP6/CMIP6_tas_land \
  reference-regions/IPCC-WGI-reference-regions-v4.geojson

# 2. ETOPO5 global relief (5 arc-min) and ERA-Interim 2 m temperature climatology
sparse NCAR/geocat-datafiles geocat \
  binary_files/ETOPO5.DAT \
  netcdf_files/T2M_ERAINT_rectilinear_grid_2D.nc

# 3. Rasterised IUCN / BirdLife / GARD range maps (0.5°)
sparse RS-eco/rasterSp rastersp \
  data/amphibians_dist.rda data/ter_birds_dist.rda data/ter_mammals_dist.rda \
  data/gard_reptiles_dist.rda data/amphibians_threatened.rda \
  data/ter_birds_threatened.rda data/ter_mammals_threatened.rda

# 4. Conservation International biodiversity hotspots (GeoJSON)
sparse rdmpage/biodiversity-hotspots hotspots .

# 5. Natural Earth
mkdir -p ne
for f in ne_50m_land ne_50m_glaciated_areas ne_110m_admin_0_countries ne_50m_admin_0_countries ne_50m_populated_places; do
  curl -sSL -o "ne/$f.geojson" \
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/$f.geojson"
done
echo "Raw data ready in $(pwd)"
