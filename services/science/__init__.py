"""Albedian Scientific Astrometry, Plate Solving, Catalog, Ephemeris, Radiometry,
Deep-Sky Astrophysics, and Coherent Trajectory Photometry Package.
"""

from services.science.catalog import (
    ALL_SKY_CATALOG_STARS,
    ALL_SKY_EXTRA_STARS,
    CONSTELLATION_EDGES,
    PINNED_CATALOG_STARS,
    STAR_BY_ID,
    STAR_BY_NAME,
    CatalogStar,
    altaz_to_icrs,
    angular_separation_deg,
    bailer_jones_cartesian_pc,
    compute_light_travel_time_description,
    get_all_sky_catalog_stars,
    get_catalog_stars,
    get_star_by_name,
    icrs_to_altaz,
    propagate_star_epoch,
    ra_dec_to_unit_vector,
    unit_vector_to_ra_dec,
)
from services.science.coherent_stack import (
    integrate_wcs_coherent_star_trajectories,
    subtract_lunar_scattering_veil,
)
from services.science.deep_sky import compute_3d_nearest_neighbors
from services.science.ephemeris import (
    EphemerisFinding,
    compute_solar_system_ephemerides,
)
from services.science.optical_flow import (
    _phase_correlation_shift,
    compute_video_pose_trajectory,
)
from services.science.radiometry import (
    compute_krisciunas_schaefer_sky_brightness,
    compute_lunar_phase_and_illumination,
    predict_sky_brightness,
)
from services.science.stellar_parameters import gaia_stellar_parameters
from services.science.solver import (
    CelestialFindingRecord,
    ObservationSolution,
    SolutionQuality,
    SourceDetection,
    VideoStackCalibrationResult,
    WCSModel,
    detect_point_sources,
    render_synthetic_starfield,
    solve_and_verify_field,
    solve_horizon_and_beacon_pose,
    stack_video_frames_for_calibration,
)

__all__ = [
    "ALL_SKY_CATALOG_STARS",
    "ALL_SKY_EXTRA_STARS",
    "CONSTELLATION_EDGES",
    "PINNED_CATALOG_STARS",
    "STAR_BY_ID",
    "STAR_BY_NAME",
    "CatalogStar",
    "CelestialFindingRecord",
    "EphemerisFinding",
    "ObservationSolution",
    "SolutionQuality",
    "SourceDetection",
    "VideoStackCalibrationResult",
    "WCSModel",
    "_phase_correlation_shift",
    "altaz_to_icrs",
    "angular_separation_deg",
    "bailer_jones_cartesian_pc",
    "compute_3d_nearest_neighbors",
    "compute_krisciunas_schaefer_sky_brightness",
    "compute_light_travel_time_description",
    "compute_lunar_phase_and_illumination",
    "compute_solar_system_ephemerides",
    "compute_video_pose_trajectory",
    "detect_point_sources",
    "gaia_stellar_parameters",
    "get_all_sky_catalog_stars",
    "get_catalog_stars",
    "get_star_by_name",
    "icrs_to_altaz",
    "integrate_wcs_coherent_star_trajectories",
    "predict_sky_brightness",
    "propagate_star_epoch",
    "ra_dec_to_unit_vector",
    "render_synthetic_starfield",
    "solve_and_verify_field",
    "solve_horizon_and_beacon_pose",
    "stack_video_frames_for_calibration",
    "subtract_lunar_scattering_veil",
    "unit_vector_to_ra_dec",
]
