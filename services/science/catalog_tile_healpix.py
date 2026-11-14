"""HEALPix nested-scheme face geometry, spherical directions, and Gaia source_id indexing.

Pure numpy functions mapping between ICRS `(ra_deg, dec_deg)`, unit 3-vectors, continuous
HEALPix base-face coordinates `(face, x, y)`, and nested pixel indices at orders `0..15`.
"""

from __future__ import annotations

import numpy as np

from services.science.catalog_tile_constants import HALF_PI

_JRLL = np.array([2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4], dtype=np.int64)
_JPLL = np.array([1, 3, 5, 7, 0, 2, 4, 6, 1, 3, 5, 7], dtype=np.int64)


def radec_to_face_xy(ra_deg, dec_deg) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Map ICRS directions to HEALPix base face (0..11) and continuous face coordinates.

    `x` and `y` lie in [0, 1] across the face, matching the integer `(ix, iy)` convention of
    the HEALPix C++ library (`loc2pix` nested branch) with nside = 1.
    """
    ra = np.radians(np.asarray(ra_deg, dtype=np.float64))
    dec = np.radians(np.asarray(dec_deg, dtype=np.float64))
    z = np.sin(dec)
    sin_theta = np.cos(dec)
    za = np.abs(z)
    tt = np.mod(ra / HALF_PI, 4.0)

    face = np.zeros(z.shape, dtype=np.int64)
    x = np.zeros(z.shape, dtype=np.float64)
    y = np.zeros(z.shape, dtype=np.float64)

    eq = za <= 2.0 / 3.0
    if np.any(eq):
        t1 = 0.5 + tt[eq]
        t2 = 0.75 * z[eq]
        jp = t1 - t2  # ascending edge line coordinate
        jm = t1 + t2  # descending edge line coordinate
        ifp = np.floor(jp).astype(np.int64)
        ifm = np.floor(jm).astype(np.int64)
        face[eq] = np.where(ifp == ifm, ifp | 4, np.where(ifp < ifm, ifp, ifm + 8))
        x[eq] = jm - ifm
        y[eq] = 1.0 - (jp - ifp)

    pol = ~eq
    if np.any(pol):
        ntt = np.minimum(np.floor(tt[pol]).astype(np.int64), 3)
        tp = tt[pol] - ntt
        # sqrt(3 (1 - |z|)) written with sin(theta) to stay accurate near the poles
        tmp = sin_theta[pol] * np.sqrt(3.0 / (1.0 + za[pol]))
        jp = tp * tmp
        jm = (1.0 - tp) * tmp
        north = z[pol] >= 0
        face[pol] = np.where(north, ntt, ntt + 8)
        x[pol] = np.where(north, 1.0 - jm, jp)
        y[pol] = np.where(north, 1.0 - jp, jm)

    return face, x, y


def face_xy_to_vec(face, x, y) -> np.ndarray:
    """Inverse of `radec_to_face_xy`: unit ICRS vectors (n, 3) from continuous face coordinates."""
    face = np.asarray(face, dtype=np.int64)
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    jr = _JRLL[face] - x - y
    north_cap = jr < 1.0
    south_cap = jr > 3.0
    nr = np.where(north_cap, jr, np.where(south_cap, 4.0 - jr, 1.0))
    tmp = nr * nr / 3.0
    z = np.where(north_cap, 1.0 - tmp, np.where(south_cap, tmp - 1.0, (2.0 - jr) * 2.0 / 3.0))
    sin_theta = np.where(
        north_cap | south_cap,
        np.sqrt(np.maximum(tmp * (2.0 - tmp), 0.0)),
        np.sqrt(np.maximum((1.0 - z) * (1.0 + z), 0.0)),
    )
    t = _JPLL[face] * nr + x - y
    t = np.where(t < 0.0, t + 8.0, t)
    t = np.where(t >= 8.0, t - 8.0, t)
    safe_nr = np.where(nr < 1e-15, 1.0, nr)
    phi = np.where(nr < 1e-15, 0.0, 0.5 * HALF_PI * t / safe_nr)
    return np.stack([sin_theta * np.cos(phi), sin_theta * np.sin(phi), z], axis=-1)


def _spread_bits(v: np.ndarray) -> np.ndarray:
    out = np.zeros_like(v)
    for b in range(16):
        out |= ((v >> b) & 1) << (2 * b)
    return out


def _compact_bits(v: np.ndarray) -> np.ndarray:
    out = np.zeros_like(v)
    for b in range(16):
        out |= ((v >> (2 * b)) & 1) << b
    return out


def face_xy_to_nest(order: int, face, x, y) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Nested pixel index at `order` plus the sub-pixel position (u, v) in [0, 1)."""
    if not 0 <= order <= 15:
        raise ValueError(f"order {order} outside 0..15")
    nside = 1 << order
    fx = np.asarray(x, dtype=np.float64) * nside
    fy = np.asarray(y, dtype=np.float64) * nside
    ix = np.clip(np.floor(fx).astype(np.int64), 0, nside - 1)
    iy = np.clip(np.floor(fy).astype(np.int64), 0, nside - 1)
    u = np.clip(fx - ix, 0.0, np.nextafter(1.0, 0.0))
    v = np.clip(fy - iy, 0.0, np.nextafter(1.0, 0.0))
    pix = (np.asarray(face, dtype=np.int64) << (2 * order)) + _spread_bits(ix) + (_spread_bits(iy) << 1)
    return pix, u, v


def nest_to_face_ixiy(order: int, pix) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    pix = np.asarray(pix, dtype=np.int64)
    face = pix >> (2 * order)
    sub = pix & ((1 << (2 * order)) - 1)
    return face, _compact_bits(sub), _compact_bits(sub >> 1)


def radec_to_nest(order: int, ra_deg, dec_deg) -> np.ndarray:
    face, x, y = radec_to_face_xy(ra_deg, dec_deg)
    pix, _, _ = face_xy_to_nest(order, face, x, y)
    return pix


def vec_to_radec(vec: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    ra = np.degrees(np.arctan2(vec[..., 1], vec[..., 0])) % 360.0
    dec = np.degrees(np.arcsin(np.clip(vec[..., 2], -1.0, 1.0)))
    return ra, dec


def radec_to_vec(ra_deg, dec_deg) -> np.ndarray:
    ra = np.radians(np.asarray(ra_deg, dtype=np.float64))
    dec = np.radians(np.asarray(dec_deg, dtype=np.float64))
    return np.stack([np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)], axis=-1)


def gaia_source_id_healpix(source_id, order: int) -> np.ndarray:
    """HEALPix nested index encoded in a Gaia source_id (level 12 lives in bits 35 and up)."""
    return np.asarray(source_id, dtype=np.int64) >> (35 + 2 * (12 - order))


__all__ = [
    "face_xy_to_nest",
    "face_xy_to_vec",
    "gaia_source_id_healpix",
    "nest_to_face_ixiy",
    "radec_to_face_xy",
    "radec_to_nest",
    "radec_to_vec",
    "vec_to_radec",
]
