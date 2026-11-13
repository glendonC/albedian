import * as THREE from 'three';
import { LUPUS3_GRADE, LUPUS3_VIEW, glslFloat } from './lupus3Grade.js';

export interface NebulaUniforms {
  [uniform: string]: THREE.IUniform<unknown>;
  uLupus3Tex: { value: THREE.Texture | null };
  uTime: { value: number };
  uIsBlackHole: { value: number };
}

export function createNebulaUniforms(): NebulaUniforms {
  return {
    uLupus3Tex: { value: null },
    uTime: { value: 0.0 },
    uIsBlackHole: { value: 0.0 },
  };
}

/**
 * REGIME 3: deep sky: the registered sky and the catalog stars and, for Sagittarius A*, a
 * hand-tuned black hole composition (an approximate Schwarzschild ray march around a hand-set
 * disk, over ESO's Lupus 3 photograph); see webglRenderLayers.ts for the disclosures. The plane
 * is hidden otherwise.
 */
export function buildDeepSkyRegimeGroup(
  group: THREE.Group,
  nebulaUniforms: NebulaUniforms
): { blackHoleMeshes: THREE.Object3D[] } {
  // The Lupus 3 photograph's grade, from the one place the Credits page words it
  const lupus3 = {
    blackLevel: glslFloat(LUPUS3_GRADE.blackLevel),
    power: glslFloat(LUPUS3_GRADE.power),
    gain: glslFloat(LUPUS3_GRADE.gain),
    ringBoost: glslFloat(LUPUS3_GRADE.ringBoost),
  };
  const volGeo = new THREE.PlaneGeometry(31, 19.5);
  const volMat = new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    blending: THREE.NormalBlending,
    uniforms: nebulaUniforms,
    vertexShader: /* glsl */ `
      varying vec2 vUv;
      varying vec3 vLocalPos;
      void main() {
        vUv = uv;
        vLocalPos = position;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: /* glsl */ `
      uniform sampler2D uLupus3Tex;
      uniform float uTime;
      uniform float uIsBlackHole;
      varying vec2 vUv;
      varying vec3 vLocalPos;

      float hash21(vec2 p) {
        p = fract(p * vec2(123.34, 456.21));
        p += dot(p, p + 45.32);
        return fract(p.x * p.y);
      }

      float noise2(vec2 p) {
        vec2 i = floor(p);
        vec2 f = fract(p);
        f = f * f * (3.0 - 2.0 * f);
        float a = hash21(i);
        float b = hash21(i + vec2(1.0, 0.0));
        float c = hash21(i + vec2(0.0, 1.0));
        float d = hash21(i + vec2(1.0, 1.0));
        return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
      }

      float fbmDisk(vec2 p) {
        float v = 0.0;
        float a = 0.5;
        for (int i = 0; i < 5; i++) {
          v += a * noise2(p);
          p *= 2.07;
          a *= 0.5;
        }
        return v;
      }

      void main() {
        vec2 p = (vUv * 2.0 - 1.0) * vec2(1.58, 1.0);

        if (uIsBlackHole > 0.5) {
          // Approximate Schwarzschild ray march (non-rotating hole, 120 steps); not a model of Sgr A*
          vec3 ro = vec3(0.0, 0.0, 15.5);
          vec3 rd = normalize(vec3(p * 0.62, -1.0));

          // Tilt accretion disk by 9.5 deg so we see the upper & lower gravitationally lensed halos
          float tilt = ${glslFloat(LUPUS3_VIEW.tiltRad)};
          float ct = cos(tilt), st = sin(tilt);
          mat3 diskRot = mat3(
            1.0, 0.0, 0.0,
            0.0, ct, -st,
            0.0, st, ct
          );
          // Slight 6-deg roll for cinema framing
          float cr = cos(-${glslFloat(LUPUS3_VIEW.rollRad)}), sr = sin(-${glslFloat(LUPUS3_VIEW.rollRad)});
          mat3 rollRot = mat3(
            cr, -sr, 0.0,
            sr, cr, 0.0,
            0.0, 0.0, 1.0
          );
          ro = diskRot * (rollRot * ro);
          rd = normalize(diskRot * (rollRot * rd));

          float rs = 1.0; // Schwarzschild radius r_s = 2GM/c^2
          vec3 pos = ro;
          vec3 vel = rd;
          vec3 hVec = cross(pos, vel);
          float h2 = dot(hVec, hVec);

          vec3 accumDisk = vec3(0.0);
          float trans = 1.0;
          bool captured = false;
          float minR = 100.0;

          for (int i = 0; i < 120; i++) {
            float r = length(pos);
            minR = min(minR, r);
            if (r < rs * 1.02) {
              captured = true;
              break;
            }
            if (r > 28.0) break;

            float dt = clamp(0.042 * r, 0.022, 0.26);
            // Schwarzschild photon bending, d^2x/dlambda^2 = -1.5 * rs * h^2 * x / r^5; renormalizing the
            // direction each step makes the bending approximate
            vec3 accel = -1.5 * rs * h2 * pos / pow(r, 5.0);
            vec3 nextVel = normalize(vel + accel * dt);
            vec3 nextPos = pos + 0.5 * (vel + nextVel) * dt;

            // Check equatorial accretion disk plane crossing (pos.y * nextPos.y <= 0.0)
            if (pos.y * nextPos.y <= 0.0) {
              float frac = abs(pos.y) / max(0.0001, abs(pos.y - nextPos.y));
              vec3 hit = mix(pos, nextPos, clamp(frac, 0.0, 1.0));
              float diskR = length(hit.xz);
              // Hand-set disk from 2.62 r_s to 9.2 r_s (a non-rotating hole's innermost stable orbit is 3 r_s)
              if (diskR > 2.62 * rs && diskR < 9.2 * rs) {
                float rNorm = (diskR - 2.62 * rs) / (6.58 * rs);
                float radialEnv = smoothstep(0.0, 0.08, rNorm) * exp(-rNorm * 3.4);

                float phi = atan(hit.z, hit.x);
                float omega = 1.65 / pow(diskR, 1.25);
                float spiralPhase = phi + uTime * omega + 11.0 / (diskR + 0.25);

                // Noise rings and spiral streaks for texture, not a simulation
                float rings = 0.52 + 0.48 * sin(diskR * 32.0 + fbmDisk(vec2(diskR * 3.8, phi * 2.0)) * 5.5);
                float streaks = fbmDisk(vec2(diskR * 5.5, spiralPhase * 2.0));
                float density = radialEnv * mix(rings, streaks, 0.52);

                // Doppler factor D = 1 / (gamma * (1 - beta * cos(theta))) at Keplerian speed, used to shift the hand-picked colours
                vec3 tangent = normalize(vec3(-hit.z, 0.0, hit.x));
                float beta = clamp(sqrt(0.5 * rs / diskR), 0.0, 0.58);
                float gamma = inversesqrt(1.0 - beta * beta);
                float cosTheta = dot(tangent, -vel);
                float doppler = 1.0 / (gamma * (1.0 - beta * cosTheta));
                float gravRedshift = sqrt(max(0.12, 1.0 - rs / diskR));
                float shift = clamp(doppler * gravRedshift, 0.48, 1.85);

                // Three hand-picked colours blended by the shift, not a blackbody spectrum
                vec3 coldRed = vec3(0.88, 0.18, 0.03);
                vec3 warmGold = vec3(1.42, 0.82, 0.30);
                vec3 hotWhiteBlue = vec3(1.75, 1.60, 1.42);
                vec3 diskCol = mix(coldRed, warmGold, smoothstep(0.55, 1.05, shift));
                diskCol = mix(diskCol, hotWhiteBlue, smoothstep(1.05, 1.65, shift));

                float stepAlpha = clamp(density * 0.92, 0.0, 0.95);
                accumDisk += trans * diskCol * pow(shift, 2.1) * stepAlpha * 1.65;
                trans *= (1.0 - stepAlpha * 0.82);
              }
            }

            pos = nextPos;
            vel = nextVel;
          }

          // A hand-set glow where a ray passes near the photon sphere (r = 1.5 r_s), not computed emission
          float photonRing = exp(-pow((minR - 1.52 * rs) / 0.065, 2.0)) * (captured ? 0.35 : 1.55);
          accumDisk += vec3(1.65, 1.22, 0.72) * photonRing;

          // Rays that escape sample ESO's Lupus 3 photograph (eso1804a) by their final direction; Lupus 3
          // is a nearby cloud, so this backdrop is not what lies behind Sgr A*
          if (!captured) {
            vec2 lensedUv = vec2(atan(vel.z, vel.x) / 6.28318 + 0.5, asin(clamp(vel.y, -1.0, 1.0)) / 3.14159 + 0.5);
            vec3 bgNeb = texture2D(uLupus3Tex, lensedUv).rgb;
            // Hand-set brightening near the photon sphere, not a magnification from the geodesics
            float ringBoost = 1.0 + ${lupus3.ringBoost} * exp(-max(0.0, minR - 1.5 * rs) * 1.4);
            accumDisk += trans * pow(max(vec3(0.0), bgNeb - ${lupus3.blackLevel}), vec3(${lupus3.power})) * ${lupus3.gain} * ringBoost;
          }

          // Anamorphic horizontal cinema flare across the bright approaching wing
          float anamorphic = exp(-abs(p.y + 0.02) * 26.0) * exp(-abs(p.x + 0.18) * 2.4) * 0.38;
          accumDisk += vec3(1.15, 0.78, 0.42) * anamorphic;

          // Tone-map while preserving deep black shadow inside the event horizon
          vec3 finalBh = accumDisk / (vec3(1.0) + accumDisk * 0.42);
          float vignette = smoothstep(1.45, 0.25, length(p * vec2(0.72, 0.95)));
          float outAlpha = clamp((captured ? 0.98 : length(finalBh) * 1.65) * vignette, 0.0, 0.98);
          gl_FragColor = vec4(finalBh * vignette, outAlpha);
          return;
        }

        // Outside the black hole view the plane is hidden and draws nothing
        discard;
      }
    `,
  });

  const blackHoleMesh = new THREE.Mesh(volGeo, volMat);
  blackHoleMesh.position.set(0, 0.3, -15.8);
  blackHoleMesh.visible = nebulaUniforms.uIsBlackHole.value > 0.5;
  group.add(blackHoleMesh);

  return { blackHoleMeshes: [blackHoleMesh] };
}
