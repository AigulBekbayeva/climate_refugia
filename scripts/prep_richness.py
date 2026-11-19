"""
Step 1 of the pipeline: turn the rasterSp species-by-cell tables (.rda) into
0.5° richness grids. Each file is parsed in its own process because the
BirdLife table is large (memory-heavy in pure Python).

Usage: python scripts/prep_richness.py raw
Writes raw/richness.npz
"""
import subprocess
import sys

import numpy as np

RAW = sys.argv[1] if len(sys.argv) > 1 else "raw"
FILES = ["amphibians_dist", "ter_birds_dist", "ter_mammals_dist", "gard_reptiles_dist",
         "amphibians_threatened", "ter_birds_threatened", "ter_mammals_threatened"]

def read_xy_species(path):
    """Minimal reader for an .rda holding one data.frame(x, y, species).
    Avoids building millions of Python strings (rdata needs >7 GB for birds)."""
    import lzma, bz2, gzip, struct
    raw = open(path, "rb").read()
    for dec in (lzma.decompress, bz2.decompress, gzip.decompress):
        try:
            buf = dec(raw)
            break
        except Exception:
            continue
    else:
        buf = raw
    del raw
    assert buf[:5] == b"RDX3\n" and buf[5:7] == b"X\n"
    pos = buf.index(b"\x00\x00\x03\x13")          # VECSXP with attributes = data.frame
    pos += 4
    ncol = struct.unpack(">i", buf[pos:pos + 4])[0]; pos += 4
    cols = []
    for c in range(3):
        flags = struct.unpack(">I", buf[pos:pos + 4])[0]; pos += 4
        typ = flags & 0xFF
        n = struct.unpack(">i", buf[pos:pos + 4])[0]; pos += 4
        if n == -1:
            hi, lo = struct.unpack(">II", buf[pos:pos + 8]); pos += 8
            n = (hi << 32) + lo
        if typ == 14:      # REALSXP
            cols.append(np.frombuffer(buf, ">f8", n, pos).astype(np.float64)); pos += 8 * n
        elif typ == 13:    # INTSXP (factor codes)
            cols.append(np.frombuffer(buf, ">i4", n, pos).astype(np.int32)); pos += 4 * n
            if flags & (1 << 9):
                pass       # attributes follow; not needed for the last column
        elif typ == 16:    # STRSXP -> integer ids
            ids = np.empty(n, np.int32); lut = {}; last = None; lid = -1
            unpack = struct.unpack_from
            for i in range(n):
                pos += 4
                L = unpack(">i", buf, pos)[0]; pos += 4
                if L > 0:
                    s_ = buf[pos:pos + L]; pos += L
                    if s_ != last:
                        lid = lut.setdefault(s_, len(lut)); last = s_
                else:
                    s_ = None; lid = lut.setdefault(None, len(lut)); last = None
                ids[i] = lid
            cols.append(ids)
        else:
            raise ValueError(f"unexpected SEXP type {typ}")
    return cols[0], cols[1], cols[2]


if len(sys.argv) > 2 and sys.argv[2] == "--one":
    name = sys.argv[3]
    x, y, sp = read_xy_species(f"{RAW}/rastersp/data/{name}.rda")
    ix = np.floor((x + 180) * 2).astype(np.int64).clip(0, 719)
    iy = np.floor((90 - y) * 2).astype(np.int64).clip(0, 359)
    key = np.unique(sp.astype(np.int64) * 360 * 720 + iy * 720 + ix)   # de-duplicate
    cell = key % (360 * 720)
    g = np.bincount(cell, minlength=360 * 720).reshape(360, 720).astype(np.int32)
    np.savez_compressed(f"{RAW}/_{name}.npz", grid=g, nspecies=len(np.unique(sp)))
    sys.exit(0)

out = {}
for f in FILES:
    print("·", f, flush=True)
    subprocess.run([sys.executable, __file__, RAW, "--one", f], check=True)
    d = np.load(f"{RAW}/_{f}.npz")
    out[f] = d["grid"]
    out[f + "_n"] = d["nspecies"]
np.savez_compressed(f"{RAW}/richness.npz", **out)
print("· wrote", f"{RAW}/richness.npz")
