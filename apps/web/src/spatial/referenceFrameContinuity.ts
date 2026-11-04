import * as THREE from 'three';

/**
 * 3-Tier Planckian Blackbody & Optical Rangefinder Palette
 * Tier 1: #F4F1EA (Starlight Alabaster)
 * Tier 2: rgba(244, 241, 234, 0.44) (Warm Pewter)
 * Tier 3: #E2B86B (Blackbody Gold)
 */
const PALETTE_STARLIGHT_ALABASTER = 0xf4f1ea;
const PALETTE_WARM_PEWTER = 0xb5b1a8;
const PALETTE_BLACKBODY_GOLD = 0xe2b86b;

const REGIME_WORLD_ANCHORS: Record<string, THREE.Vector3> = {
  earth_capture: new THREE.Vector3(0, 0, 0),
  cislunar: new THREE.Vector3(0, 42, -160),
  solar_neighborhood: new THREE.Vector3(0, 95, -380),
  deep_sky: new THREE.Vector3(0, 150, -620),
  planetary_surface: new THREE.Vector3(0, 210, -880),
};

interface OriginFrustumMaterials {
  pyramidLineMat: THREE.LineBasicMaterial;
  goldAccentMat: THREE.LineBasicMaterial;
  horizonRingMat: THREE.LineBasicMaterial;
  sightlineDashMat: THREE.LineDashedMaterial;
  waypointPointsMat: THREE.PointsMaterial;
  frustumSubgroup: THREE.Group;
  sightlineLine: THREE.Line;
  waypointPoints: THREE.Points;
  horizonRingGroup: THREE.Group;
}

/**
 * Formats Right Ascension and Declination in degrees into optical rangefinder
 * sexagesimal notation (e.g. `α 05h 55m · δ +07° 24′`).
 */
export function formatRaDecSexagesimal(raDeg: number, decDeg: number): string {
  const normalizedRa = (((raDeg % 360) + 360) % 360) / 15;
  const raHours = Math.floor(normalizedRa);
  const raMinutes = Math.floor((normalizedRa - raHours) * 60);

  const sign = decDeg >= 0 ? '+' : '−';
  const absDec = Math.min(90, Math.abs(decDeg));
  const decDegrees = Math.floor(absDec);
  const decArcmin = Math.floor((absDec - decDegrees) * 60);

  const hh = String(raHours).padStart(2, '0');
  const mm = String(raMinutes).padStart(2, '0');
  const dd = String(decDegrees).padStart(2, '0');
  const am = String(decArcmin).padStart(2, '0');

  return `α ${hh}h ${mm}m · δ ${sign}${dd}° ${am}′`;
}

/**
 * Builds a cinema-grade Three.js `THREE.Group` representing the ghosted
 * camera-FOV frustum & origin sightline indicator (Plummer 2014/2022 reference-frame
 * coordination between the earthly capture horizon and 3D cosmic regimes).
 */
export function createOriginCameraFrustumIndicator(): THREE.Group {
  const rootGroup = new THREE.Group();
  rootGroup.name = 'OriginCameraFrustumIndicator';
  rootGroup.visible = false;

  const frustumSubgroup = new THREE.Group();
  frustumSubgroup.name = 'GhostedCaptureFrustum';
  rootGroup.add(frustumSubgroup);

  // 1. 35mm / Field-Camera FOV Frustum Pyramid (Apex at observer origin -> sensor frame)
  const frameDepth = -2.25;
  const halfW = 1.12;
  const halfH = 0.7;

  const pyramidVertices = new Float32Array([
    // 4 Pyramid rays from earthly observer nodal apex (0,0,0) to sensor frame corners
    0, 0, 0, -halfW, -halfH, frameDepth,
    0, 0, 0, halfW, -halfH, frameDepth,
    0, 0, 0, halfW, halfH, frameDepth,
    0, 0, 0, -halfW, halfH, frameDepth,
    // Outer sensor frame rectangle
    -halfW, -halfH, frameDepth, halfW, -halfH, frameDepth,
    halfW, -halfH, frameDepth, halfW, halfH, frameDepth,
    halfW, halfH, frameDepth, -halfW, halfH, frameDepth,
    -halfW, halfH, frameDepth, -halfW, -halfH, frameDepth,
  ]);

  const pyramidGeo = new THREE.BufferGeometry();
  pyramidGeo.setAttribute('position', new THREE.BufferAttribute(pyramidVertices, 3));

  const pyramidLineMat = new THREE.LineBasicMaterial({
    color: PALETTE_STARLIGHT_ALABASTER,
    transparent: true,
    opacity: 0.26,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });

  const pyramidLines = new THREE.LineSegments(pyramidGeo, pyramidLineMat);
  frustumSubgroup.add(pyramidLines);

  // 2. Optical Rangefinder Corner Ticks, Edge Fiducials & Split-Image Center Reticle
  const insetW = halfW * 0.88;
  const insetH = halfH * 0.86;
  const cornerLen = 0.22;
  const reticleR = 0.14;
  const tickLen = 0.08;

  const goldAccentCoords: number[] = [
    // Top-left L bracket
    -insetW, insetH, frameDepth, -insetW + cornerLen, insetH, frameDepth,
    -insetW, insetH, frameDepth, -insetW, insetH - cornerLen, frameDepth,
    // Top-right L bracket
    insetW, insetH, frameDepth, insetW - cornerLen, insetH, frameDepth,
    insetW, insetH, frameDepth, insetW, insetH - cornerLen, frameDepth,
    // Bottom-right L bracket
    insetW, -insetH, frameDepth, insetW - cornerLen, -insetH, frameDepth,
    insetW, -insetH, frameDepth, insetW, -insetH + cornerLen, frameDepth,
    // Bottom-left L bracket
    -insetW, -insetH, frameDepth, -insetW + cornerLen, -insetH, frameDepth,
    -insetW, -insetH, frameDepth, -insetW, -insetH + cornerLen, frameDepth,
    // Mid-frame optical rangefinder fiducial ticks
    0, halfH, frameDepth, 0, halfH - tickLen, frameDepth,
    0, -halfH, frameDepth, 0, -halfH + tickLen, frameDepth,
    -halfW, 0, frameDepth, -halfW + tickLen, 0, frameDepth,
    halfW, 0, frameDepth, halfW - tickLen, 0, frameDepth,
    // Center reticle crosshairs (broken center for optical split-image clarity)
    -reticleR * 1.45, 0, frameDepth, -reticleR * 0.45, 0, frameDepth,
    reticleR * 0.45, 0, frameDepth, reticleR * 1.45, 0, frameDepth,
    0, -reticleR * 1.45, frameDepth, 0, -reticleR * 0.45, frameDepth,
    0, reticleR * 0.45, frameDepth, 0, reticleR * 1.45, frameDepth,
  ];

  // Octagonal micro-ring around center reticle
  const ringSegments = 16;
  for (let i = 0; i < ringSegments; i++) {
    const a0 = (i / ringSegments) * Math.PI * 2;
    const a1 = ((i + 1) / ringSegments) * Math.PI * 2;
    goldAccentCoords.push(
      Math.cos(a0) * reticleR,
      Math.sin(a0) * reticleR,
      frameDepth,
      Math.cos(a1) * reticleR,
      Math.sin(a1) * reticleR,
      frameDepth
    );
  }

  const goldAccentGeo = new THREE.BufferGeometry();
  goldAccentGeo.setAttribute(
    'position',
    new THREE.Float32BufferAttribute(goldAccentCoords, 3)
  );

  const goldAccentMat = new THREE.LineBasicMaterial({
    color: PALETTE_BLACKBODY_GOLD,
    transparent: true,
    opacity: 0.56,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });

  const goldAccentLines = new THREE.LineSegments(goldAccentGeo, goldAccentMat);
  frustumSubgroup.add(goldAccentLines);

  // 3. Earthly Observer Horizon Ring around the Frustum Nodal Apex (0, 0, 0)
  const horizonRingGroup = new THREE.Group();
  horizonRingGroup.name = 'ObserverHorizonRing';
  const horizonRadius = 0.46;
  const horizonCoords: number[] = [];
  const horizonSegs = 32;
  for (let i = 0; i < horizonSegs; i++) {
    const a0 = (i / horizonSegs) * Math.PI * 2;
    const a1 = ((i + 1) / horizonSegs) * Math.PI * 2;
    horizonCoords.push(
      Math.cos(a0) * horizonRadius,
      0,
      Math.sin(a0) * horizonRadius,
      Math.cos(a1) * horizonRadius,
      0,
      Math.sin(a1) * horizonRadius
    );
  }
  // Cardinal zenith/azimuth ticks on the observer horizon ring
  for (let c = 0; c < 4; c++) {
    const ang = (c / 4) * Math.PI * 2;
    const rIn = horizonRadius * 0.82;
    const rOut = horizonRadius * 1.16;
    horizonCoords.push(
      Math.cos(ang) * rIn,
      0,
      Math.sin(ang) * rIn,
      Math.cos(ang) * rOut,
      0,
      Math.sin(ang) * rOut
    );
  }

  const horizonGeo = new THREE.BufferGeometry();
  horizonGeo.setAttribute(
    'position',
    new THREE.Float32BufferAttribute(horizonCoords, 3)
  );
  const horizonRingMat = new THREE.LineBasicMaterial({
    color: PALETTE_WARM_PEWTER,
    transparent: true,
    opacity: 0.34,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  const horizonLines = new THREE.LineSegments(horizonGeo, horizonRingMat);
  horizonRingGroup.add(horizonLines);
  frustumSubgroup.add(horizonRingGroup);

  // 4. Receding Origin Sightline Ray (Dashed Optical Filament connecting capture to 3D target)
  const sightlinePositions = new Float32Array([
    0, 0, 0,
    0, 0, frameDepth,
    0, 0, -12.5,
  ]);
  const sightlineGeo = new THREE.BufferGeometry();
  sightlineGeo.setAttribute(
    'position',
    new THREE.BufferAttribute(sightlinePositions, 3)
  );

  const sightlineDashMat = new THREE.LineDashedMaterial({
    color: PALETTE_BLACKBODY_GOLD,
    transparent: true,
    opacity: 0.48,
    dashSize: 0.38,
    gapSize: 0.22,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });

  const sightlineLine = new THREE.Line(sightlineGeo, sightlineDashMat);
  sightlineLine.computeLineDistances();
  rootGroup.add(sightlineLine);

  // 5. Optical Photon-Waypoint Markers along the Sightline Ray
  const waypointCount = 9;
  const waypointPositions = new Float32Array(waypointCount * 3);
  for (let i = 0; i < waypointCount; i++) {
    const t = i / (waypointCount - 1);
    waypointPositions[i * 3] = 0;
    waypointPositions[i * 3 + 1] = 0;
    waypointPositions[i * 3 + 2] = -t * 12.5;
  }
  const waypointGeo = new THREE.BufferGeometry();
  waypointGeo.setAttribute(
    'position',
    new THREE.BufferAttribute(waypointPositions, 3)
  );

  const waypointPointsMat = new THREE.PointsMaterial({
    color: PALETTE_BLACKBODY_GOLD,
    size: 0.085,
    transparent: true,
    opacity: 0.62,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });

  const waypointPoints = new THREE.Points(waypointGeo, waypointPointsMat);
  rootGroup.add(waypointPoints);

  const storedMaterials: OriginFrustumMaterials = {
    pyramidLineMat,
    goldAccentMat,
    horizonRingMat,
    sightlineDashMat,
    waypointPointsMat,
    frustumSubgroup,
    sightlineLine,
    waypointPoints,
    horizonRingGroup,
  };
  rootGroup.userData.originContinuity = storedMaterials;

  return rootGroup;
}

/**
 * Smoothly animates and positions the ghosted camera-FOV frustum & origin sightline
 * indicator during portal flight transitions (`warpFactor > 0`) and in `cislunar`,
 * `solar_neighborhood`, `deep_sky`, and `planetary_surface` regimes.
 * Hidden in steady-state `earth_capture` when `warpFactor < 0.02`.
 */
export function updateOriginFrustumContinuity(
  group: THREE.Group,
  activeRegime: string,
  warpFactor: number,
  elapsedSec: number,
  observerLatDeg: number,
  observerLonDeg: number
): void {
  const clampedWarp = Math.max(0, Math.min(1, warpFactor));
  const isEarthSteadyState = activeRegime === 'earth_capture' && clampedWarp < 0.02;

  if (isEarthSteadyState) {
    group.visible = false;
    return;
  }

  group.visible = true;

  const state = group.userData.originContinuity as OriginFrustumMaterials | undefined;
  if (!state) return;

  // If the group was attached directly to the Scene instead of localFrame, anchor it to the active regime
  const baseWorldOffset =
    group.parent && group.parent.type === 'Scene'
      ? (REGIME_WORLD_ANCHORS[activeRegime] ?? REGIME_WORLD_ANCHORS.earth_capture!)
      : new THREE.Vector3(0, 0, 0);

  group.position.copy(baseWorldOffset);

  // Regime depth factor: 0 in earth_capture, increasing gently across 02 Orbit -> 05 Surface
  const regimeIndex =
    activeRegime === 'cislunar'
      ? 1
      : activeRegime === 'solar_neighborhood'
        ? 2
        : activeRegime === 'deep_sky'
          ? 3
          : activeRegime === 'planetary_surface'
            ? 4
            : 0;

  const steadyPresence = regimeIndex > 0 ? 1.0 : 0.0;
  const visibilityBlend = Math.min(1.0, Math.max(clampedWarp * 1.25, steadyPresence));

  // Pull back smoothly from center-axis during portal flight into a clean lower-left rangefinder anchor
  const pullbackT = regimeIndex > 0 ? 1.0 - clampedWarp * 0.45 : clampedWarp;
  const originPos = new THREE.Vector3(
    -2.35 * pullbackT,
    -1.28 * pullbackT,
    -3.2 - pullbackT * 2.6 - regimeIndex * 0.28
  );

  // Target 3D regime subject anchor in camera-forward space
  const targetAnchor = new THREE.Vector3(
    0,
    regimeIndex === 4 ? 0.35 : 0.12,
    -12.8
  );

  state.frustumSubgroup.position.copy(originPos);

  // Aim the ghosted capture frustum along the sightline toward the 3D subject
  const sightDir = targetAnchor.clone().sub(originPos).normalize();
  const defaultForward = new THREE.Vector3(0, 0, -1);
  const alignQuat = new THREE.Quaternion().setFromUnitVectors(defaultForward, sightDir);
  state.frustumSubgroup.quaternion.copy(alignQuat);

  // Scale down subtly as we move deeper into space regimes so it reads as a receding earthly origin
  const frustumScale = Math.max(0.46, 0.78 - regimeIndex * 0.065 + clampedWarp * 0.14);
  state.frustumSubgroup.scale.setScalar(frustumScale);

  // Orient the earthly observer horizon ring by observer latitude & sidereal longitude
  const latRad = (observerLatDeg * Math.PI) / 180;
  const lonRad = (observerLonDeg * Math.PI) / 180;
  state.horizonRingGroup.rotation.x = Math.max(-0.55, Math.min(0.55, latRad * 0.35));
  state.horizonRingGroup.rotation.y = lonRad * 0.25 + elapsedSec * 0.08;

  // Update the Origin Sightline Ray geometry connecting the earthly frustum apex to the 3D target anchor
  const frameCenterWorld = new THREE.Vector3(0, 0, -2.25 * frustumScale)
    .applyQuaternion(alignQuat)
    .add(originPos);

  const linePosAttr = state.sightlineLine.geometry.getAttribute(
    'position'
  ) as THREE.BufferAttribute | undefined;
  if (linePosAttr) {
    linePosAttr.setXYZ(0, originPos.x, originPos.y, originPos.z);
    linePosAttr.setXYZ(1, frameCenterWorld.x, frameCenterWorld.y, frameCenterWorld.z);
    linePosAttr.setXYZ(2, targetAnchor.x, targetAnchor.y, targetAnchor.z);
    linePosAttr.needsUpdate = true;
    state.sightlineLine.computeLineDistances();
  }

  // Animate photon packets streaming along the sightline tether from the earthly frustum to the 3D subject
  const wpAttr = state.waypointPoints.geometry.getAttribute(
    'position'
  ) as THREE.BufferAttribute | undefined;
  if (wpAttr) {
    const count = wpAttr.count;
    const phase = (elapsedSec * (0.28 + clampedWarp * 0.65)) % 1.0;
    for (let i = 0; i < count; i++) {
      const t = i === 0 ? 0 : ((i - 1 + phase) / (count - 1)) % 1.0;
      const px = THREE.MathUtils.lerp(originPos.x, targetAnchor.x, t);
      const py = THREE.MathUtils.lerp(originPos.y, targetAnchor.y, t);
      const pz = THREE.MathUtils.lerp(originPos.z, targetAnchor.z, t);
      wpAttr.setXYZ(i, px, py, pz);
    }
    wpAttr.needsUpdate = true;
  }

  // Subtle harmonic optical pulse
  const pulse = 0.92 + 0.08 * Math.sin(elapsedSec * 2.2);
  state.pyramidLineMat.opacity = 0.26 * visibilityBlend * pulse;
  state.goldAccentMat.opacity = 0.56 * visibilityBlend * pulse;
  state.horizonRingMat.opacity = 0.34 * visibilityBlend;
  state.sightlineDashMat.opacity = (0.38 + clampedWarp * 0.28) * visibilityBlend;
  state.waypointPointsMat.opacity = (0.52 + clampedWarp * 0.32) * visibilityBlend;
}
