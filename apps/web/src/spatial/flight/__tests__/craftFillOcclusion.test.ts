import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import { FILL_AO_ATTRIBUTE, FILL_AO_DEFINE, FILL_AO_FRAGMENT, fillOcclusionShader, markFillOcclusion } from '../craftFillOcclusion.js';
import { addEdgeLight, createEdgeLightUniforms, EDGE_FRAGMENT } from '../craftEdgeLight.js';

// craft-ao-fill-one (DECIDED-BY-LEAD 25 Nov 07:21 and 08:00 on ASSETS craft-ao-one): the sky's fill shadowed by the craft
// itself, carried per vertex as the attribute _AO in the EMU, the Z-2 and New Horizons, applied to the fill alone: the
// light from the sky (three's indirect diffuse and indirect specular, from the environment), never the key, the second
// light or the edge light.

/** A box whose every vertex carries the fill's occlusion, as three's glTF loader names ASSETS' _AO. */
const withAo = (_n: number, value = 0.5) => {
  const g = new THREE.BoxGeometry();
  const count = g.getAttribute('position').count;
  g.setAttribute(FILL_AO_ATTRIBUTE, new THREE.Float32BufferAttribute(new Array(count).fill(value), 1));
  return g;
};

/** Three's own shader text with every #include put in place, as WebGLProgram does before compiling. */
function resolved(src: string): string {
  return src.replace(/^[ \t]*#include +<([\w\d./]+)>/gm, (_m, name: string) => {
    const chunk = (THREE.ShaderChunk as Record<string, string>)[name];
    if (chunk === undefined) throw new Error(`no chunk ${name}`);
    return resolved(chunk);
  });
}

describe('which of a model\'s materials take the fill\'s occlusion', () => {
  it('marks a standard material whose every mesh carries _ao, and counts the meshes', () => {
    const a = new THREE.MeshStandardMaterial();
    const b = new THREE.MeshPhysicalMaterial();
    const model = new THREE.Group();
    model.add(new THREE.Mesh(withAo(1), a), new THREE.Mesh(withAo(1), b));
    expect(markFillOcclusion(model)).toEqual({ meshes: 2, occluded: 2 });
    expect(a.defines?.[FILL_AO_DEFINE]).toBe('');
    expect(b.defines?.[FILL_AO_DEFINE]).toBe('');
    // three's own defines kept
    expect(a.defines?.STANDARD).toBe('');
    expect(b.defines?.PHYSICAL).toBe('');
  });

  it('leaves a mesh without _ao as it was, since an unbound attribute reads 0 and would shut the fill off', () => {
    const lone = new THREE.MeshStandardMaterial();
    const shared = new THREE.MeshStandardMaterial();
    const model = new THREE.Group();
    model.add(new THREE.Mesh(new THREE.BoxGeometry(), lone), new THREE.Mesh(withAo(1), shared), new THREE.Mesh(new THREE.BoxGeometry(), shared));
    expect(markFillOcclusion(model)).toEqual({ meshes: 3, occluded: 0 });
    expect(lone.defines?.[FILL_AO_DEFINE]).toBeUndefined();
    expect(shared.defines?.[FILL_AO_DEFINE]).toBeUndefined();
  });

  it('marks a skinned mesh as any other, and leaves a material three does not light as it is', () => {
    const suit = new THREE.MeshStandardMaterial();
    const unlit = new THREE.MeshBasicMaterial();
    const skinned = new THREE.SkinnedMesh(withAo(1), suit);
    const model = new THREE.Group();
    model.add(skinned, new THREE.Mesh(withAo(1), unlit));
    expect(markFillOcclusion(model)).toEqual({ meshes: 2, occluded: 1 });
    expect(suit.defines?.[FILL_AO_DEFINE]).toBe('');
    expect(unlit.defines?.[FILL_AO_DEFINE]).toBeUndefined();
  });

  it('asks a marked material for a new program, and marks it once however often it is asked', () => {
    const m = new THREE.MeshStandardMaterial();
    const model = new THREE.Mesh(withAo(1), m);
    const before = m.version;
    markFillOcclusion(model);
    expect(m.version).toBeGreaterThan(before);
    const after = m.version;
    expect(markFillOcclusion(model)).toEqual({ meshes: 1, occluded: 1 });
    expect(m.version).toBe(after);
  });
});

describe('the fill\'s occlusion in three\'s standard shader', () => {
  const patched = () => {
    const shader = { uniforms: {} as Record<string, unknown>, fragmentShader: THREE.ShaderLib.physical.fragmentShader, vertexShader: THREE.ShaderLib.physical.vertexShader };
    fillOcclusionShader(shader);
    return shader;
  };

  it('reads _ao per vertex and hands it to the fragment, only where the material is marked', () => {
    const { vertexShader, fragmentShader } = patched();
    const v = resolved(vertexShader);
    const decl = v.indexOf(`attribute float ${FILL_AO_ATTRIBUTE};`);
    expect(decl).toBeGreaterThan(0);
    expect(v.lastIndexOf(`#ifdef ${FILL_AO_DEFINE}`, decl)).toBeGreaterThan(0);
    expect(v.indexOf('vFillAo = _ao;')).toBeGreaterThan(v.indexOf('void main()'));
    expect(resolved(fragmentShader).indexOf('varying float vFillAo;')).toBeGreaterThan(0);
  });

  it('shadows the sky\'s light after three has gathered it and before the output adds it up', () => {
    const f = resolved(patched().fragmentShader);
    const at = f.indexOf(FILL_AO_FRAGMENT);
    expect(at).toBeGreaterThan(0);
    // after the environment's irradiance and radiance are gathered (lights_fragment_end) and three's own aoMap
    expect(at).toBeGreaterThan(f.indexOf('RE_IndirectSpecular( radiance, iblIrradiance, clearcoatRadiance, geometryPosition, geometryNormal, geometryViewDir, geometryClearcoatNormal, material, reflectedLight );'));
    expect(at).toBeGreaterThan(f.indexOf('float ambientOcclusion = '));
    // before the totals are summed
    expect(at).toBeLessThan(f.indexOf('vec3 totalDiffuse = reflectedLight.directDiffuse + reflectedLight.indirectDiffuse;'));
  });

  it('touches the indirect light alone, as three\'s own aoMap does, never the direct light of the key or the second light', () => {
    expect(FILL_AO_FRAGMENT).toContain(`#ifdef ${FILL_AO_DEFINE}`);
    expect(FILL_AO_FRAGMENT).toContain('reflectedLight.indirectDiffuse *= fillAo;');
    expect(FILL_AO_FRAGMENT).toContain('reflectedLight.indirectSpecular *= computeSpecularOcclusion(');
    expect(FILL_AO_FRAGMENT).toContain('clearcoatSpecularIndirect *= fillAo;');
    expect(FILL_AO_FRAGMENT).toContain('sheenSpecularIndirect *= fillAo;');
    expect(FILL_AO_FRAGMENT).not.toMatch(/\.direct(Diffuse|Specular)/);
    expect(FILL_AO_FRAGMENT).not.toContain('outgoingLight');
    // the occlusion kept between shut and open
    expect(FILL_AO_FRAGMENT).toContain('float fillAo = saturate( vFillAo );');
  });

  it('leaves the edge light, which is the key\'s, outside the occlusion', () => {
    const uniforms = createEdgeLightUniforms();
    const material = new THREE.MeshStandardMaterial();
    const model = new THREE.Mesh(withAo(1), material);
    markFillOcclusion(model);
    addEdgeLight(model, uniforms);
    const shader = { uniforms: {} as Record<string, unknown>, fragmentShader: THREE.ShaderLib.physical.fragmentShader, vertexShader: THREE.ShaderLib.physical.vertexShader };
    material.onBeforeCompile(shader as never, undefined as never);
    const f = resolved(shader.fragmentShader);
    expect(f.indexOf(FILL_AO_FRAGMENT)).toBeGreaterThan(0);
    expect(f.indexOf(EDGE_FRAGMENT)).toBeGreaterThan(f.indexOf('vec3 outgoingLight = '));
    expect(resolved(shader.vertexShader)).toContain('vFillAo = _ao;');
    expect(material.defines?.[FILL_AO_DEFINE]).toBe('');
  });
});
