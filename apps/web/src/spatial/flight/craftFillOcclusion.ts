import * as THREE from 'three';

/**
 * craft-ao-fill-one (DECIDED-BY-LEAD 25 Nov 07:21 and 08:00 on ASSETS craft-ao-one): the sky's fill shadowed by the craft
 * itself. ASSETS baked it per vertex into the EMU, the Z-2 and New Horizons at every level, as the glTF attribute _AO
 * (1 where the half sphere about the vertex is open, 0 where the craft shuts it, from rays within a reach), which three's
 * glTF loader names `_ao` and does not read as a colour. It is applied here to the fill alone, the light from the sky
 * (the environment's irradiance and radiance, three's indirect diffuse and indirect specular), exactly as three r187's
 * own aoMap applies an occlusion texture (aomap_fragment); the key, the second light and the edge light are the
 * scene's direct light and stay as they were. No vertex moves and no colour changes.
 */

/** The attribute three's GLTFLoader makes of ASSETS' `_AO` (an unknown attribute's name, lowercased). */
export const FILL_AO_ATTRIBUTE = '_ao';
/** The define that turns the occlusion on in one material's program. */
export const FILL_AO_DEFINE = 'CRAFT_FILL_AO';

const VERTEX_DECLARATIONS = `#ifdef ${FILL_AO_DEFINE}\nattribute float ${FILL_AO_ATTRIBUTE};\nvarying float vFillAo;\n#endif`;
const VERTEX_MAIN = `#ifdef ${FILL_AO_DEFINE}\nvFillAo = ${FILL_AO_ATTRIBUTE};\n#endif`;
const FRAGMENT_DECLARATIONS = `#ifdef ${FILL_AO_DEFINE}\nvarying float vFillAo;\n#endif`;

/**
 * The lines put just after three's own aoMap: the sky's light, gathered by then into the indirect terms, shadowed by the
 * vertex's occlusion as three shadows it by an aoMap (the specular by three's own computeSpecularOcclusion).
 */
export const FILL_AO_FRAGMENT = [
  `#ifdef ${FILL_AO_DEFINE}`,
  'float fillAo = saturate( vFillAo );',
  'reflectedLight.indirectDiffuse *= fillAo;',
  '#if defined( USE_CLEARCOAT )',
  'clearcoatSpecularIndirect *= fillAo;',
  '#endif',
  '#if defined( USE_SHEEN )',
  'sheenSpecularIndirect *= fillAo;',
  '#endif',
  '#if defined( USE_ENVMAP ) && defined( STANDARD )',
  'reflectedLight.indirectSpecular *= computeSpecularOcclusion( saturate( dot( geometryNormal, geometryViewDir ) ), fillAo, material.roughness );',
  '#endif',
  '#endif',
].join('\n');

/** Edits three's standard shader text in place (from a material's onBeforeCompile); a shader without the hooks is left as it is. */
export function fillOcclusionShader(shader: { vertexShader: string; fragmentShader: string }): void {
  shader.vertexShader = shader.vertexShader
    .replace('#include <common>', `#include <common>\n${VERTEX_DECLARATIONS}`)
    .replace('#include <uv_vertex>', `#include <uv_vertex>\n${VERTEX_MAIN}`);
  shader.fragmentShader = shader.fragmentShader
    .replace('#include <common>', `#include <common>\n${FRAGMENT_DECLARATIONS}`)
    .replace('#include <aomap_fragment>', `#include <aomap_fragment>\n${FILL_AO_FRAGMENT}`);
}

const MARKED = new WeakSet<THREE.Material>();

/**
 * Marks each standard material of a model all of whose meshes carry the occlusion, before its programs are made, and
 * says how many of the model's meshes are drawn with it. A material that also draws a mesh without the attribute is left
 * unmarked: WebGL reads an attribute no buffer feeds as 0, which would shut the fill off there.
 */
export function markFillOcclusion(model: THREE.Object3D): { meshes: number; occluded: number } {
  const meshesOf = new Map<THREE.Material, THREE.Mesh[]>();
  const all: THREE.Mesh[] = [];
  model.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (!mesh.isMesh) return;
    all.push(mesh);
    for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
      if (!material) continue;
      const list = meshesOf.get(material) ?? [];
      list.push(mesh);
      meshesOf.set(material, list);
    }
  });
  const carries = (mesh: THREE.Mesh) => mesh.geometry?.getAttribute(FILL_AO_ATTRIBUTE)?.itemSize === 1;
  const takes = (material: THREE.Material) => material instanceof THREE.MeshStandardMaterial && meshesOf.get(material)!.every(carries);
  for (const material of meshesOf.keys()) {
    if (!takes(material) || MARKED.has(material)) continue;
    MARKED.add(material);
    material.defines = { ...material.defines, [FILL_AO_DEFINE]: '' };
    material.needsUpdate = true;
  }
  const occluded = all.filter((mesh) => carries(mesh) && (Array.isArray(mesh.material) ? mesh.material : [mesh.material]).every((m) => !!m && takes(m))).length;
  return { meshes: all.length, occluded };
}
