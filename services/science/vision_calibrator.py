"""Scene source classifier and optional Claude Vision scene verifier (`services/science/vision_calibrator.py`).

Distinguishes true upper-sky `celestial_beacons` (compact high-SNR celestial sources in the
upper sky above `horizon_y`) from `artificial_ground_lights` (extended/saturated campfires,
tent lanterns, or horizon lights near or below `horizon_y`).
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import numpy as np
from scipy import ndimage

from services.science.detector import _to_luma_2d


def _try_claude_vision_scene_notes(
    celestial_beacons: list[dict[str, Any]],
    artificial_lights: list[dict[str, Any]],
    horizon_y: float,
    height: int,
) -> str | None:
    """Optionally query Anthropic Claude API for multimodal scene verification summary when enabled."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key or os.environ.get("ALBEDIAN_ENABLE_LIVE_CLAUDE", "0") != "1":
        return None

    try:
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-3-5-haiku-latest",
                "max_tokens": 128,
                "messages": [
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "task": "Summarize night-sky scene source classification",
                                "horizonY": horizon_y,
                                "height": height,
                                "celestialBeaconCount": len(celestial_beacons),
                                "artificialGroundLightCount": len(artificial_lights),
                            }
                        ),
                    }
                ],
            },
            timeout=2.5,
        )
        if resp.status_code == 200:
            return str(resp.json().get("content", [{}])[0].get("text", "")).strip() or None
    except Exception:
        return None
    return None


def classify_night_scene_sources(
    luma: np.ndarray,
    horizon_y: float,
) -> dict[str, Any]:
    """Classify bright luminance peaks into `celestial_beacons` vs `artificial_ground_lights`.

    Parameters
    ----------
    luma:
        2D grayscale or 3D RGB image array.
    horizon_y:
        Estimated vertical horizon row coordinate in pixels.

    Returns
    -------
    dict[str, Any]
        Dictionary containing `celestial_beacons` (`celestialBeacons`),
        `artificial_ground_lights` (`artificialGroundLights`), `horizonY`, and `sceneSummary`.
    """
    arr = _to_luma_2d(luma)
    h, w = arr.shape
    hz_y = float(np.clip(horizon_y, 8.0, float(h - 8)))

    # Upper sky ceiling for true celestial beacons (well above horizon clutter)
    upper_sky_limit_y = min(hz_y - 12.0, float(h) * 0.42)
    sky_slice_h = max(8, int(round(max(12.0, upper_sky_limit_y))))
    sky_region = arr[:sky_slice_h, :]
    sky_med = float(np.median(sky_region))
    sky_mad = float(np.median(np.abs(sky_region - sky_med))) * 1.4826
    sky_sigma = max(sky_mad, 1e-4 * max(1.0, float(np.max(arr))))

    smoothed = ndimage.gaussian_filter(arr, sigma=1.8, mode="nearest")
    local_max = ndimage.maximum_filter(smoothed, size=15, mode="nearest")
    thresh = sky_med + 6.0 * sky_sigma
    is_peak = (smoothed == local_max) & (smoothed >= thresh)

    margin = 4
    is_peak[:margin, :] = False
    is_peak[-margin:, :] = False
    is_peak[:, :margin] = False
    is_peak[:, -margin:] = False

    pys, pxs = np.nonzero(is_peak)
    celestial_beacons: list[dict[str, Any]] = []
    artificial_ground_lights: list[dict[str, Any]] = []

    for y0, x0 in zip(pys.tolist(), pxs.tolist()):
        peak_val = float(smoothed[y0, x0])
        snr = (peak_val - sky_med) / sky_sigma

        # Measure spatial extent (number of pixels above half-prominence in a 17x17 patch)
        r_win = 8
        y_a, y_b = max(0, y0 - r_win), min(h, y0 + r_win + 1)
        x_a, x_b = max(0, x0 - r_win), min(w, x0 + r_win + 1)
        patch = arr[y_a:y_b, x_a:x_b]
        half_level = sky_med + 0.5 * (peak_val - sky_med)
        area_px = int(np.count_nonzero(patch >= half_level))

        entry = {
            "xPx": float(x0),
            "yPx": float(y0),
            "snr": round(float(snr), 2),
            "peakLuma": round(float(peak_val), 4),
            "halfMaxAreaPx": area_px,
        }

        if float(y0) < upper_sky_limit_y and area_px <= 110 and snr >= 8.0:
            entry["sourceClass"] = "celestial_beacon"
            celestial_beacons.append(entry)
        elif float(y0) >= (hz_y - 16.0) or area_px > 110:
            entry["sourceClass"] = "artificial_ground_light"
            artificial_ground_lights.append(entry)

    celestial_beacons.sort(key=lambda item: item["snr"], reverse=True)
    artificial_ground_lights.sort(key=lambda item: item["peakLuma"], reverse=True)

    claude_note = _try_claude_vision_scene_notes(
        celestial_beacons, artificial_ground_lights, hz_y, h
    )
    summary = claude_note or (
        f"Classified {len(celestial_beacons)} upper-sky celestial beacon(s) (y < {upper_sky_limit_y:.0f} px) "
        f"and {len(artificial_ground_lights)} artificial/horizon ground light(s) near/below y = {hz_y:.0f} px."
    )

    return {
        "horizonY": round(hz_y, 2),
        "upperSkyLimitY": round(upper_sky_limit_y, 2),
        "celestial_beacons": celestial_beacons,
        "celestialBeacons": celestial_beacons,
        "artificial_ground_lights": artificial_ground_lights,
        "artificialGroundLights": artificial_ground_lights,
        "sceneSummary": summary,
    }
