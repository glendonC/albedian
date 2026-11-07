import * as THREE from 'three';

export function resolveSkyTargetPosition(
  id: string,
  title: string,
  raDeg: number,
  decDeg: number
): THREE.Vector3 {
  const lowerId = id.toLowerCase();
  const lowerTitle = title.toLowerCase();
  if (lowerTitle.includes('moon')) {
    return new THREE.Vector3(5.4, 2.8, -26.0);
  }
  if (lowerId.includes('alpha_centauri') || lowerTitle.includes('alpha centauri')) {
    return new THREE.Vector3(-3.8, 4.4, -25.4);
  }
  if (lowerId.includes('carina') || lowerTitle.includes('carina')) {
    return new THREE.Vector3(2.8, 3.6, -25.6);
  }
  if (lowerId.includes('acrux') || lowerTitle.includes('acrux') || lowerTitle.includes('southern cross')) {
    return new THREE.Vector3(-0.6, 5.1, -25.4);
  }
  if (lowerId.includes('canopus') || lowerTitle.includes('canopus')) {
    return new THREE.Vector3(6.8, 1.4, -25.8);
  }
  if (lowerId.includes('altair') || lowerTitle.includes('altair')) {
    return new THREE.Vector3(-6.4, 4.6, -25.0);
  }
  if (lowerId.includes('tarazed') || lowerTitle.includes('tarazed')) {
    return new THREE.Vector3(-8.8, 6.2, -25.2);
  }
  if (lowerId.includes('alshain') || lowerTitle.includes('alshain')) {
    return new THREE.Vector3(-4.2, 2.8, -25.2);
  }
  if (lowerId.includes('albireo') || lowerTitle.includes('albireo')) {
    return new THREE.Vector3(-10.5, 8.2, -24.8);
  }
  if (lowerId.includes('deneb_algedi') || lowerTitle.includes('deneb algedi')) {
    return new THREE.Vector3(1.8, 0.2, -25.8);
  }
  if (lowerId.includes('sadalsuud') || lowerTitle.includes('sadalsuud')) {
    return new THREE.Vector3(0.4, 4.2, -25.6);
  }
  if (lowerId.includes('dabih') || lowerTitle.includes('dabih')) {
    return new THREE.Vector3(-2.6, -1.2, -25.8);
  }
  if (lowerId.includes('algedi') || lowerTitle.includes('algedi')) {
    return new THREE.Vector3(-4.8, -0.2, -25.8);
  }
  if (lowerId.includes('nashira') || lowerTitle.includes('nashira')) {
    return new THREE.Vector3(0.2, -1.6, -25.8);
  }
  if (lowerId.includes('fomalhaut') || lowerTitle.includes('fomalhaut')) {
    return new THREE.Vector3(4.2, -3.4, -26.2);
  }
  if (lowerId.includes('saturn') || lowerTitle.includes('saturn')) {
    return new THREE.Vector3(3.1, 5.4, -25.8);
  }
  if (lowerId.includes('uranus') || lowerTitle.includes('uranus')) {
    return new THREE.Vector3(8.8, 3.6, -25.5);
  }
  if (lowerId.includes('vega') || lowerTitle.includes('vega')) {
    return new THREE.Vector3(-10.2, 7.4, -24.5);
  }
  if (lowerId.includes('betelgeuse') || lowerTitle.includes('betelgeuse')) {
    return new THREE.Vector3(-4.2, 3.8, -25.5);
  }
  if (lowerId.includes('rigel') || lowerTitle.includes('rigel')) {
    return new THREE.Vector3(-1.4, -2.1, -26.0);
  }
  if (lowerId.includes('aldebaran') || lowerTitle.includes('aldebaran')) {
    return new THREE.Vector3(4.6, 5.6, -25.2);
  }
  if (lowerId.includes('sirius') || lowerTitle.includes('sirius')) {
    return new THREE.Vector3(-7.6, -3.2, -25.5);
  }
  if (lowerId.includes('jupiter') || lowerTitle.includes('jupiter')) {
    return new THREE.Vector3(2.4, 6.4, -25.4);
  }
  if (lowerId.includes('sgr_a') || lowerTitle.includes('sagittarius') || lowerTitle.includes('black hole')) {
    return new THREE.Vector3(0.8, 3.2, -25.4);
  }
  const normRa = (((raDeg - 80 + 540) % 360) - 180) / 180;
  const normDec = Math.max(-45, Math.min(65, decDeg)) / 65;
  return new THREE.Vector3(normRa * 11.5, normDec * 6.5 + 1.2, -26.0);
}

export function projectCatalogDirectionToScreen(
  camera: THREE.PerspectiveCamera,
  sceneDirection: THREE.Vector3,
  viewportWidth: number,
  viewportHeight: number
): { x: number; y: number; visible: boolean } {
  camera.updateMatrixWorld();
  const camPos = camera.getWorldPosition(new THREE.Vector3());
  const worldPos = camPos.addScaledVector(sceneDirection, 100);
  const camSpace = worldPos.clone().applyMatrix4(camera.matrixWorldInverse);
  if (camSpace.z >= 0) return { x: -999, y: -999, visible: false };
  const ndc = worldPos.project(camera);
  const x = (ndc.x * 0.5 + 0.5) * viewportWidth;
  const y = (-ndc.y * 0.5 + 0.5) * viewportHeight;
  return { x, y, visible: x >= 0 && x <= viewportWidth && y >= 0 && y <= viewportHeight };
}

export function projectSkyWorldPointToScreen(
  camera: THREE.PerspectiveCamera,
  worldPos: THREE.Vector3,
  viewportWidth: number,
  viewportHeight: number
): { x: number; y: number; visible: boolean } {
  camera.updateMatrixWorld();
  const camSpace = worldPos.clone().applyMatrix4(camera.matrixWorldInverse);
  if (camSpace.z >= -0.2) {
    return { x: -999, y: -999, visible: false };
  }
  const ndc = worldPos.clone().project(camera);
  const x = (ndc.x * 0.5 + 0.5) * viewportWidth;
  const y = (-ndc.y * 0.5 + 0.5) * viewportHeight;
  const visible =
    x >= 32 &&
    x <= viewportWidth - 32 &&
    y >= 56 &&
    y <= viewportHeight - 64;
  return { x, y, visible };
}

export function computeCameraTelemetrySnapshot(params: {
  camera: THREE.PerspectiveCamera;
  activeRegime: string;
  deepSkyAnchor: THREE.Vector3;
  cislunarMoonMesh: THREE.Mesh | null;
  betelgeuseMesh: THREE.Mesh | null;
  planetaryParentBodyMesh: THREE.Mesh | null;
  curYaw: number;
  curPitch: number;
  curFov: number;
  velYaw: number;
  velPitch: number;
  warpFactor: number;
  viewportWidth: number;
  viewportHeight: number;
}) {
  const rawAz = ((-params.curYaw * 180) / Math.PI + 210) % 360;
  const yawDeg = (rawAz + 360) % 360;
  const pitchDeg = Math.round(((params.curPitch * 180) / Math.PI + 24) * 10) / 10;
  const fovDeg = Math.round(params.curFov);

  let subjectWorld = new THREE.Vector3(0, 0, -10);
  if (params.activeRegime === 'cislunar' && params.cislunarMoonMesh) {
    subjectWorld = params.cislunarMoonMesh.getWorldPosition(new THREE.Vector3());
  } else if (params.activeRegime === 'solar_neighborhood' && params.betelgeuseMesh) {
    subjectWorld = params.betelgeuseMesh.getWorldPosition(new THREE.Vector3());
  } else if (params.activeRegime === 'deep_sky') {
    subjectWorld = new THREE.Vector3(0, 0.3, -13.5).add(params.deepSkyAnchor);
  } else if (params.activeRegime === 'planetary_surface' && params.planetaryParentBodyMesh) {
    subjectWorld = params.planetaryParentBodyMesh.getWorldPosition(new THREE.Vector3());
  }

  params.camera.updateMatrixWorld();
  const camSpace = subjectWorld.clone().applyMatrix4(params.camera.matrixWorldInverse);
  let subjectScreen = { x: params.viewportWidth * 0.5, y: params.viewportHeight * 0.5, visible: false };
  if (params.activeRegime !== 'earth_capture' && camSpace.z < -0.5) {
    const ndc = subjectWorld.clone().project(params.camera);
    subjectScreen = {
      x: (ndc.x * 0.5 + 0.5) * params.viewportWidth,
      y: (-ndc.y * 0.5 + 0.5) * params.viewportHeight,
      visible: true,
    };
  }

  return {
    yawDeg,
    pitchDeg,
    fovDeg,
    velYaw: params.velYaw,
    velPitch: params.velPitch,
    warpFactor: params.warpFactor,
    subjectScreen,
  };
}
