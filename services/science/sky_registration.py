"""Astrometric registration of an all-sky plate carree panorama to catalog stars.

Pure functions only (no file I/O). `scripts/register_sky_panorama.py` measures the star
images of ESO's Milky Way panorama (eso0932a) with these and writes the solution the web
renderers use to sample it by direction; `apps/web/src/spatial/sky/skyRegistration.ts`
mirrors the projection and the offset table defined here.

Model, from an ICRS unit vector i to a panorama pixel (x right, y down, both continuous with
pixel k spanning [k, k + 1)):

    g = ICRS_TO_GALACTIC @ i                       galactic frame
    p = R @ g                                      the panorama's own frame (fitted rotation R)
    lon, lat = atan2(p_y, p_x), asin(p_z)
    x = W * frac(0.5 - lon / 2 pi)                 longitude 0 at the centre, increasing to the left
    y = H * (0.5 - lat / pi) + y_offset            north up, plus a fitted vertical offset
    then a smooth offset table (east, north) in texels at the equator, so the star lands at
    x - east / cos(lat), y - north

The panorama is a 300-field mosaic stitched by hand-held panorama software, not a map made in
exact galactic coordinates: its axes sit about 3.8 degrees from the galactic ones and its
stitching leaves smooth distortions of a texel or two. The rotation and vertical offset take
out the first; the table, fitted to the residuals of catalog stars and checked on held-out
stars, takes out most of the second.

Implementation is split across focused acyclic sibling modules:
- `sky_reg_frames`: coordinate frames, unit-vector / tangent helpers, plate carree projection
- `sky_reg_centroids`: wrapped difference-of-Gaussians filter and subpixel star centroiding
- `sky_reg_fitting`: Kabsch rotation, rigid residuals, soft-L1 solver, tangent-plane offsets
- `sky_reg_offsets`: spherical Gaussian kernel regression and offset-table codec / sampling
"""

from services.science.sky_reg_centroids import (
    CENTROID_BOX_HALF,
    CENTROID_ITERATIONS,
    CENTROID_WINDOW_PX,
    difference_of_gaussians,
    star_centroid,
)
from services.science.sky_reg_fitting import (
    RigidSolution,
    fit_rigid,
    kabsch,
    model_pixels,
    pixel_residuals,
    tangent_offsets,
)
from services.science.sky_reg_frames import (
    ICRS_TO_GALACTIC,
    NGP_DEC_DEG,
    NGP_RA_DEG,
    NODE_LON_DEG,
    PanoramaGeometry,
    east_north,
    frame_to_pixel,
    lon_lat,
    pixel_to_frame,
    radec_to_unit,
    unit_from_lon_lat,
    wrap_dx,
)
from services.science.sky_reg_offsets import (
    OFFSET_TABLE_BIAS,
    apply_offsets,
    decode_offset_table,
    encode_offset_table,
    offset_table_frame,
    sample_offset_table,
    smooth_offsets,
)

__all__ = [
    "CENTROID_BOX_HALF",
    "CENTROID_ITERATIONS",
    "CENTROID_WINDOW_PX",
    "ICRS_TO_GALACTIC",
    "NGP_DEC_DEG",
    "NGP_RA_DEG",
    "NODE_LON_DEG",
    "OFFSET_TABLE_BIAS",
    "PanoramaGeometry",
    "RigidSolution",
    "apply_offsets",
    "decode_offset_table",
    "difference_of_gaussians",
    "east_north",
    "encode_offset_table",
    "fit_rigid",
    "frame_to_pixel",
    "kabsch",
    "lon_lat",
    "model_pixels",
    "offset_table_frame",
    "pixel_residuals",
    "pixel_to_frame",
    "radec_to_unit",
    "sample_offset_table",
    "smooth_offsets",
    "star_centroid",
    "tangent_offsets",
    "unit_from_lon_lat",
    "wrap_dx",
]
