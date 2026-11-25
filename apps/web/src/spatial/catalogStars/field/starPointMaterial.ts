import * as THREE from 'three';

const CATALOG_STAR_VERTEX_SHADER = /* glsl */ `
      attribute float aMagnitude;
      attribute vec3 aTint;
      uniform float uLimitingMagnitude;
      uniform float uPixelRatio;
      varying vec3 vTint;
      varying float vAlpha;
      varying float vGain;
      varying float vSpike;
      void main() {
        vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
        gl_Position = projectionMatrix * mvPosition;
        // Catalog stars fainter than the limiting magnitude fade out over its last 0.3 mag
        float fade = clamp((uLimitingMagnitude - aMagnitude) / 0.3, 0.0, 1.0);
        // Size, opacity and gain follow catalog flux relative to a 6th magnitude star:
        // fainter stars dim, brighter stars grow and run hot enough to bloom
        float rel = pow(10.0, -0.4 * (aMagnitude - 6.0));
        vAlpha = clamp(sqrt(rel), 0.05, 1.0) * fade;
        vGain = clamp(pow(rel, 0.18), 1.0, 3.5);
        vTint = aTint;
        vSpike = aMagnitude < 1.0 ? 1.0 : 0.0;
        float grown = 1.9 * pow(rel, 0.3);
        float size = clamp(grown, 1.5, 22.0) * mix(1.0, 2.2, vSpike);
        // predsky-starless-one: below the 1.5 px floor the sprite keeps its size and gives up opacity for the area it
        // was lent, so the light a faint star draws keeps following its magnitude (no floor lifts it); no star that
        // grows past the floor (brighter than about 6.9) changes
        vAlpha = vAlpha * min(1.0, (grown / 1.5) * (grown / 1.5));
        gl_PointSize = fade > 0.0 ? size * uPixelRatio : 0.0;
      }
    `;

const CATALOG_STAR_FRAGMENT_SHADER = /* glsl */ `
      varying vec3 vTint;
      varying float vAlpha;
      varying float vGain;
      varying float vSpike;
      void main() {
        vec2 uv = gl_PointCoord * 2.0 - 1.0;
        float d = length(uv);
        if (d > 1.0) discard;
        // Spike sprites are drawn larger; scale the radius so the core keeps its size
        float r = d * mix(1.0, 2.2, vSpike);
        float core = exp(-r * r * 3.2);
        float halo = exp(-r * 3.0) * 0.3;
        float spikes = 0.0;
        if (vSpike > 0.5) {
          float sx = exp(-abs(uv.y) * 42.0) * exp(-abs(uv.x) * 3.8);
          float sy = exp(-abs(uv.x) * 42.0) * exp(-abs(uv.y) * 3.8);
          spikes = (sx + sy) * 0.55;
        }
        float intensity = (core + halo + spikes) * vAlpha * vGain;
        vec3 col = mix(vTint, vec3(1.0), clamp(core * 0.6, 0.0, 1.0)) * intensity;
        // Additive blending multiplies by alpha, so intensity lives in rgb only (applying it
        // twice would square it and erase faint stars); values above 1 feed the bloom pass
        gl_FragColor = vec4(col, 1.0);
      }
    `;

export function createCatalogStarMaterial(limitingMagnitude: number): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    uniforms: {
      uLimitingMagnitude: { value: limitingMagnitude },
      uPixelRatio: { value: 1 },
    },
    vertexShader: CATALOG_STAR_VERTEX_SHADER,
    fragmentShader: CATALOG_STAR_FRAGMENT_SHADER,
  });
}

export function setStarMaterialLimitingMagnitude(material: THREE.ShaderMaterial, limitingMagnitude: number): void {
  material.uniforms.uLimitingMagnitude!.value = limitingMagnitude;
}

export function setStarMaterialPixelRatio(material: THREE.ShaderMaterial, pixelRatio: number): void {
  material.uniforms.uPixelRatio!.value = pixelRatio;
}
