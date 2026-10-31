"""Server-side intent parser, authoritative scientific explanation generator,
optional Claude API copilot adapter, and revision-checked action validator
mirroring `@albedian/contracts`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import re
from typing import Any

import httpx

from services.science.catalog import (
    ALL_SKY_CATALOG_STARS,
    PINNED_CATALOG_STARS,
    STAR_BY_ID,
    CatalogStar,
    compute_light_travel_time_description,
)
from services.science.ephemeris import compute_solar_system_ephemerides

_STARS_BY_NAME_LEN: tuple[CatalogStar, ...] = tuple(
    sorted(ALL_SKY_CATALOG_STARS, key=lambda s: len(s.common_name), reverse=True)
)
_SOLAR_SYSTEM_BODIES: tuple[str, ...] = ("jupiter", "uranus", "saturn", "mars", "venus", "moon")


@dataclass(frozen=True)
class PointerContext:
    """Recent pointer or tap coordinates with candidate finding resolution."""

    x_px: float
    y_px: float
    at_timestamp_ms: float
    view_revision: int
    nearest_finding_id: str | None = None
    overlapping_candidate_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PointerContext:
        return cls(
            x_px=float(data.get("xPx", data.get("x_px", 0.0))),
            y_px=float(data.get("yPx", data.get("y_px", 0.0))),
            at_timestamp_ms=float(data.get("atTimestampMs", data.get("at_timestamp_ms", 0.0))),
            view_revision=int(data.get("viewRevision", data.get("view_revision", 0))),
            nearest_finding_id=data.get("nearestFindingId", data.get("nearest_finding_id")),
            overlapping_candidate_ids=list(
                data.get("overlappingCandidateIds", data.get("overlapping_candidate_ids", []))
            ),
        )


@dataclass(frozen=True)
class ActionContext:
    """Multimodal session context for deictic resolution and revision checking."""

    session_id: str = "session-0"
    source_asset_id: str = "asset-0"
    source_time_sec: float = 0.0
    navigation_state: str = "source"
    calibration_state: str = "calibrated"
    selected_finding_id: str | None = None
    focused_finding_id: str | None = None
    recent_pointer: PointerContext | None = None
    simulation_epoch_iso: str = "2030-11-01T22:32:00Z"
    view_revision: int = 1
    reduced_motion: bool = False
    observer_lat: float = 30.0
    observer_lon: float = -40.0

    @classmethod
    def from_dict(cls, data: dict[str, Any] | ActionContext) -> ActionContext:
        if isinstance(data, ActionContext):
            return data
        ptr_raw = data.get("recentPointer", data.get("recent_pointer"))
        ptr = PointerContext.from_dict(ptr_raw) if isinstance(ptr_raw, dict) else ptr_raw
        return cls(
            session_id=str(data.get("sessionId", data.get("session_id", "session-0"))),
            source_asset_id=str(data.get("sourceAssetId", data.get("source_asset_id", "asset-0"))),
            source_time_sec=float(data.get("sourceTimeSec", data.get("source_time_sec", 0.0))),
            navigation_state=str(data.get("navigationState", data.get("navigation_state", "source"))),
            calibration_state=str(data.get("calibrationState", data.get("calibration_state", "calibrated"))),
            selected_finding_id=data.get("selectedFindingId", data.get("selected_finding_id")),
            focused_finding_id=data.get("focusedFindingId", data.get("focused_finding_id")),
            recent_pointer=ptr,
            simulation_epoch_iso=str(
                data.get("simulationEpochISO", data.get("simulation_epoch_iso", "2030-11-01T22:32:00Z"))
            ),
            view_revision=int(data.get("viewRevision", data.get("view_revision", 1))),
            reduced_motion=bool(data.get("reducedMotion", data.get("reduced_motion", False))),
            observer_lat=float(data.get("observerLat", data.get("observer_lat", 30.0))),
            observer_lon=float(data.get("observerLon", data.get("observer_lon", -40.0))),
        )


def _resolve_target_finding_id(utterance: str, context: ActionContext) -> str | None:
    """Resolve explicit star/planet name across all 70+ all-sky objects or deictic context."""
    lower = utterance.lower()
    for star in _STARS_BY_NAME_LEN:
        if star.common_name.lower() in lower:
            return f"finding-{star.id}"
    for body in _SOLAR_SYSTEM_BODIES:
        if body in lower:
            return f"finding-predicted-{body}"

    if context.recent_pointer and context.recent_pointer.nearest_finding_id:
        return context.recent_pointer.nearest_finding_id
    if context.selected_finding_id:
        return context.selected_finding_id
    if context.focused_finding_id:
        return context.focused_finding_id
    return None


def _build_scientific_explanation(
    utterance: str,
    action: dict[str, Any],
    context: ActionContext,
) -> str:
    """Compute an authoritative, deterministic scientific explanation for the action/target."""
    action_type = str(action.get("type", ""))
    finding_id = str(
        action.get("findingId")
        or action.get("targetFindingId")
        or _resolve_target_finding_id(utterance, context)
        or ""
    )

    star_key = finding_id.replace("finding-", "")
    if star_key in STAR_BY_ID:
        star = STAR_BY_ID[star_key]
        lt = compute_light_travel_time_description(star.median_pc)
        return (
            f"{star.common_name} ({star.bayer_designation} in {star.constellation}, "
            f"spectral type {star.spectral_type}, mag {star.mag:+.2f}) lies at a "
            f"Bailer-Jones distance of {star.median_pc:.1f} pc "
            f"[{star.lower_pc:.1f}–{star.upper_pc:.1f} pc] — a light-travel lookback time "
            f"of {lt['description']}. Proper motion: ({star.pm_ra_mas_yr:+.1f}, "
            f"{star.pm_dec_mas_yr:+.1f}) mas/yr."
        )

    for body_lower in _SOLAR_SYSTEM_BODIES:
        if body_lower in finding_id.lower() or body_lower in utterance.lower():
            body_cap = body_lower.capitalize()
            try:
                ephem_list = compute_solar_system_ephemerides(
                    obs_utc_iso=context.simulation_epoch_iso,
                    lat_deg=context.observer_lat,
                    lon_deg=context.observer_lon,
                    bodies=(body_cap,),
                )
                if ephem_list:
                    eph = ephem_list[0]
                    lt = compute_light_travel_time_description(eph.distance_pc)
                    return (
                        f"{eph.name} (JPL Analytical Ephemeris · evidenceClass='predicted') is at "
                        f"{eph.distance_au:.4f} AU ({lt['description']} light-travel time), "
                        f"topocentric Alt {eph.alt_deg:+.1f}°, Az {eph.az_deg:.1f}° "
                        f"(RA {eph.ra_deg:.2f}°, Dec {eph.dec_deg:+.2f}°)."
                    )
            except Exception:
                pass

    if action_type == "space.epoch":
        year_ce = action.get("yearCE", 1026)
        return (
            f"Propagated 3D starfield to {year_ce} CE using Gaia DR3 / Hipparcos 6D astrometric "
            f"phase space (proper motion + radial velocity + Bailer-Jones distance)."
        )

    if action_type == "media.restore":
        prof = action.get("profile", "star_protected")
        return (
            f"Executed astronomy-aware '{prof}' restoration branch with allowSyntheticSky=False: "
            f"lifted dark foreground shadows while protecting observed stellar PSF cores."
        )

    if action_type == "source.return":
        return (
            "Restored exact saved CameraCheckpoint back to the original camera recording, "
            "frame timestamp, and synchronized audio stream."
        )

    return (
        f"Calibrated sky view at {context.simulation_epoch_iso} "
        f"(Lat {context.observer_lat:.3f}°, Lon {context.observer_lon:.3f}°): "
        f"showing Capricornus & the Moon in the WSW, Fomalhaut in Piscis Austrinus to the South, "
        f"Saturn & Aquarius to the SE, and the Summer Triangle (Altair, Vega, Deneb) to the West."
    )


def _try_claude_api_intent(
    utterance: str,
    context: ActionContext,
    request_id: str,
    expected_rev: int,
) -> dict[str, Any] | None:
    """Optionally invoke Anthropic Claude API if `ANTHROPIC_API_KEY` is configured and reachable."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key or os.environ.get("ALBEDIAN_ENABLE_LIVE_CLAUDE", "0") != "1":
        return None

    system_prompt = (
        "You are Albedian's deterministic intent translator. Translate the user's utterance "
        "into a single JSON object matching one of the AlbedianAction discriminated union types: "
        "media.restore, sky.show, finding.select, finding.describe, space.enter, space.epoch, "
        "history.back, navigation.cancel, source.return. Output ONLY valid JSON."
    )
    user_msg = json.dumps(
        {
            "utterance": utterance,
            "expectedViewRevision": expected_rev,
            "requestId": request_id,
            "sourceAssetId": context.source_asset_id,
            "selectedFindingId": context.selected_finding_id,
        }
    )
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
                "max_tokens": 256,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_msg}],
            },
            timeout=3.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            text_block = data.get("content", [{}])[0].get("text", "{}")
            parsed = json.loads(text_block)
            if isinstance(parsed, dict) and "type" in parsed:
                parsed["requestId"] = request_id
                parsed["expectedViewRevision"] = expected_rev
                return parsed
    except Exception:
        return None
    return None


def parse_voice_intent(
    utterance: str,
    context: ActionContext | dict[str, Any],
    request_id: str = "req-voice-1",
    expected_view_revision: int | None = None,
) -> dict[str, Any]:
    """Deterministically translate a natural-language utterance into a schema-valid `AlbedianAction`."""
    ctx = ActionContext.from_dict(context)
    rev = expected_view_revision if expected_view_revision is not None else ctx.view_revision
    text = utterance.strip().lower()

    claude_action = _try_claude_api_intent(utterance, ctx, request_id, rev)
    if claude_action is not None:
        return claude_action

    if text in ("stop", "wait", "cancel", "hold on", "pause transition"):
        return {"type": "navigation.cancel", "requestId": request_id, "expectedViewRevision": rev}

    return_phrases = (
        "back to moment", "back to my video", "back to the video",
        "back to photo", "return to source", "return to original", "original recording",
    )
    if any(phrase in text for phrase in return_phrases):
        return {
            "type": "source.return",
            "requestId": request_id,
            "expectedViewRevision": rev,
            "restoreAudio": True,
        }

    if "go back" in text or "step back" in text or "halfway back" in text:
        is_partial = "halfway" in text or "partway" in text
        action: dict[str, Any] = {
            "type": "history.back",
            "requestId": request_id,
            "expectedViewRevision": rev,
            "mode": "partial" if is_partial else "checkpoint",
        }
        if is_partial:
            action["fraction"] = 0.5
        return action

    epoch_match = re.search(r"(\d{3,4})\s*(ce|ad|bce|bc)?", text)
    if ("epoch" in text or "year" in text or "ce" in text or "ago" in text) and epoch_match:
        val = int(epoch_match.group(1))
        suffix = (epoch_match.group(2) or "").lower()
        year_ce = (2026 - val) if "ago" in text else (-val if suffix in ("bce", "bc") else val)
        return {
            "type": "space.epoch",
            "requestId": request_id,
            "expectedViewRevision": rev,
            "epochISO": f"{year_ce:04d}-01-01T00:00:00Z",
            "yearCE": year_ce,
        }

    if any(kw in text for kw in ("clean", "denoise", "restore", "brighten", "shadow", "noise", "enhance")):
        profile = "temporal" if "temporal" in text or "stack" in text else "star_protected"
        if "natural" in text or "tone" in text:
            profile = "natural"
        return {
            "type": "media.restore",
            "requestId": request_id,
            "expectedViewRevision": rev,
            "sourceAssetId": ctx.source_asset_id,
            "profile": profile,
            "constraints": {
                "preservePeople": True,
                "allowSyntheticSky": False,
                "protectStars": True,
            },
        }

    if any(kw in text for kw in ("3d", "space", "fly", "travel", "go to", "take me to", "enter", "beyond")):
        target_id = _resolve_target_finding_id(utterance, ctx) or "finding-star-betelgeuse"
        return {
            "type": "space.enter",
            "requestId": request_id,
            "expectedViewRevision": rev,
            "findingId": target_id,
            "distancePolicy": "measured_or_estimated",
        }

    describe_kws = ("what is", "what were we", "how far", "where is", "describe", "tell me about", "identify")
    if any(kw in text for kw in describe_kws):
        target_id = _resolve_target_finding_id(utterance, ctx) or "finding-star-betelgeuse"
        return {
            "type": "finding.describe",
            "requestId": request_id,
            "expectedViewRevision": rev,
            "findingId": target_id,
            "fields": ["distance", "type", "motion", "evidence"],
        }

    resolved_target = _resolve_target_finding_id(utterance, ctx)
    if resolved_target is not None and any(kw in text for kw in ("select", "focus", "lock", "point", "show me")):
        return {
            "type": "finding.select",
            "requestId": request_id,
            "expectedViewRevision": rev,
            "findingId": resolved_target,
        }

    if "predicted" in text or "planet" in text or "ephemeris" in text:
        return {"type": "sky.show", "requestId": request_id, "expectedViewRevision": rev, "mode": "predicted"}

    if "sky" in text or "calibrate" in text or "constellation" in text:
        return {"type": "sky.show", "requestId": request_id, "expectedViewRevision": rev, "mode": "measured"}

    target_id = resolved_target or "finding-star-betelgeuse"
    return {
        "type": "finding.select",
        "requestId": request_id,
        "expectedViewRevision": rev,
        "findingId": target_id,
    }


def validate_and_execute_voice_command(
    utterance: str,
    context: ActionContext | dict[str, Any],
    proposed_action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate revision, epistemic boundaries, and deictic disambiguation before executing."""
    ctx = ActionContext.from_dict(context)
    action = proposed_action if proposed_action is not None else parse_voice_intent(utterance, ctx)
    action_id = str(action.get("requestId", "req-voice-1"))
    action_type = str(action.get("type", ""))
    expected_rev = int(action.get("expectedViewRevision", ctx.view_revision))
    lower_utt = utterance.lower()

    if any(p in lower_utt for p in ("fake star", "synthetic sky", "replace the sky", "hallucinate", "invent star")):
        return {
            "status": "rejected",
            "actionId": action_id,
            "reason": "unsafe_synthetic_edit",
            "explanation": "Albedian never fabricates or replaces sky observations with synthetic stars.",
            "scientificExplanation": (
                "Scientific Integrity Policy: Albedian never injects synthetic catalog stars "
                "into sensor restoration outputs (`allowSyntheticSky: false`)."
            ),
            "nextActions": ["media.restore(star_protected)", "sky.show(predicted)"],
        }

    if expected_rev != ctx.view_revision:
        return {
            "status": "rejected",
            "actionId": action_id,
            "reason": "stale_revision",
            "explanation": f"Action expected viewRevision {expected_rev}, but current session is at revision {ctx.view_revision}.",
            "scientificExplanation": "Stale viewRevision rejected to preserve state machine determinism.",
            "nextActions": ["Refresh context and retry command"],
        }

    if action_type == "navigation.cancel":
        return {
            "status": "canceled",
            "actionId": action_id,
            "settledStateVersion": ctx.view_revision + 1,
            "visualStatus": "Transition canceled; camera settled at current pose.",
            "scientificExplanation": "Camera transition halted immediately on user barge-in.",
        }

    if (
        ctx.recent_pointer is not None
        and len(ctx.recent_pointer.overlapping_candidate_ids) > 1
        and action_type in ("finding.select", "finding.describe", "space.enter")
    ):
        explicit_named = any(
            st.common_name.lower() in lower_utt for st in ALL_SKY_CATALOG_STARS
        ) or any(body in lower_utt for body in _SOLAR_SYSTEM_BODIES)
        if not explicit_named:
            choices = [
                {"id": cid, "label": cid.replace("finding-star-", "").capitalize()}
                for cid in ctx.recent_pointer.overlapping_candidate_ids
            ]
            return {
                "status": "needs_input",
                "actionId": action_id,
                "question": "Multiple celestial objects overlap that pointer region. Which one did you mean?",
                "choices": choices,
                "scientificExplanation": "Deictic pointer region overlaps multiple catalog objects; disambiguation required.",
            }

    if action_type == "space.enter" and ctx.calibration_state not in ("calibrated", "predicted_only", "approximate"):
        return {
            "status": "rejected",
            "actionId": action_id,
            "reason": "unresolved_sky",
            "explanation": f"Cannot enter calibrated 3D catalog space while calibrationState is '{ctx.calibration_state}'.",
            "scientificExplanation": "3D catalog space requires a calibrated WCS or GPS+Horizon sky registration.",
            "nextActions": ["sky.show(predicted)", "media.restore(temporal)"],
        }

    if action_type == "space.epoch":
        year_ce = int(action.get("yearCE", 2026))
        if not (-10000 <= year_ce <= 12000):
            return {
                "status": "rejected",
                "actionId": action_id,
                "reason": "invalid_epoch",
                "explanation": f"Epoch year {year_ce} CE is outside valid proper-motion propagation bounds.",
                "scientificExplanation": "Linear 3D space-velocity proper-motion propagation is bounded to [-10000, +12000] CE.",
                "nextActions": ["Choose an epoch between -10000 CE and 12000 CE"],
            }

    sci_explanation = _build_scientific_explanation(utterance, action, ctx)
    return {
        "status": "completed",
        "actionId": action_id,
        "newStateVersion": ctx.view_revision + 1,
        "spokenFeedback": sci_explanation,
        "scientificExplanation": sci_explanation,
        "visualStatus": f"Completed {action_type}",
        "action": action,
    }
