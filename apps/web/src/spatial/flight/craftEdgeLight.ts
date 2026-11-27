import * as THREE from 'three';
import type { Vec3 } from './craftLight.js';
import { fillOcclusionShader } from './craftFillOcclusion.js';

/**
 * chase-light-one (lead's answer on craft-alive-one: from behind the craft shows its form, a rim from the star ahead):
 * when the key star shines from behind the craft, the suit's cloth and the craft's foil and paint scatter its light
 * forward at their edges, so the figure is ringed with the star's own light where a plain directional light gives
 * nothing (its light reaches only the far side). The edge light: the surface's own colour times the star's tint, at
 * EDGE_SHARE of the key, growing toward the silhouette as (1 - facing) to the EDGE_POWER, and only as much as the star
 * is behind the craft from the camera. Its one source is the key; nothing is added when the star is beside or behind
 * the camera.
 */
export const EDGE_SHARE = 1;
export const EDGE_POWER = 3;

/** The uniforms every patched material of one renderer shares, set once a frame. */
export interface EdgeLightUniforms {
  readonly uEdgeColor: { value: THREE.Color };
  readonly uEdgePower: { value: number };
}

export function createEdgeLightUniforms(): EdgeLightUniforms {
  return { uEdgeColor: { value: new THREE.Color(0, 0, 0) }, uEdgePower: { value: EDGE_POWER } };
}

const PATCHED = new WeakSet<THREE.Material>();

/** The fragment lines the edge light adds just before the material's output (three r187's MeshStandardMaterial). */
export const EDGE_FRAGMENT = 'outgoingLight += diffuseColor.rgb * uEdgeColor * pow(1.0 - saturate(dot(normal, normalize(vViewPosition))), uEdgePower);';

/** Patches each standard material of a model once, sharing `uniforms`; other materials are left as they are. */
export function addEdgeLight(model: THREE.Object3D, uniforms: EdgeLightUniforms): number {
  let patched = 0;
  model.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) return;
    for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
      if (!(material instanceof THREE.MeshStandardMaterial) || PATCHED.has(material)) continue;
      PATCHED.add(material);
      material.onBeforeCompile = (shader) => {
        shader.uniforms.uEdgeColor = uniforms.uEdgeColor;
        shader.uniforms.uEdgePower = uniforms.uEdgePower;
        shader.fragmentShader = shader.fragmentShader
          .replace('#include <common>', '#include <common>\nuniform vec3 uEdgeColor;\nuniform float uEdgePower;')
          .replace('#include <opaque_fragment>', `${EDGE_FRAGMENT}\n#include <opaque_fragment>`);
        // craft-ao-fill-one: the sky fill's occlusion, in the programs of the materials markFillOcclusion marked
        fillOcclusionShader(shader);
      };
      material.customProgramCacheKey = () => 'craft-edge-light';
      material.needsUpdate = true;
      patched += 1;
    }
  });
  return patched;
}

/**
 * The edge light's colour for a frame: the key's tint at EDGE_SHARE of the key's radiance on a white surface facing
 * it (1 / pi for an irradiance of 1), times how far the star is behind the craft from the camera (the cosine between
 * the camera's way to the craft and the way to the key; 0 when the key is beside or behind the camera).
 */
export function edgeLightColor(cameraToCraft: Vec3, toKey: Vec3, keyTint: Vec3, share = EDGE_SHARE): Vec3 {
  const n = Math.hypot(cameraToCraft[0], cameraToCraft[1], cameraToCraft[2]) || 1;
  const behind = Math.max(0, (cameraToCraft[0] * toKey[0] + cameraToCraft[1] * toKey[1] + cameraToCraft[2] * toKey[2]) / n);
  const k = (share * behind) / Math.PI;
  return [keyTint[0] * k, keyTint[1] * k, keyTint[2] * k];
}
