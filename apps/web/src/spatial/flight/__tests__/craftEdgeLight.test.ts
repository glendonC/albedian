import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import { EDGE_FRAGMENT, EDGE_POWER, EDGE_SHARE, addEdgeLight, createEdgeLightUniforms, edgeLightColor } from '../craftEdgeLight.js';

// chase-light-one (lead's answer on craft-alive-one: from behind, the craft shows its form, a rim from the star ahead):
// the star's light scattered forward at the craft's edges, only as much as the star is behind the craft from the camera.

describe('the edge light\'s colour', () => {
  const tint: [number, number, number] = [1, 0.8, 0.6];

  it('is the key\'s tint at its share of a white face\'s radiance when the star is straight behind the craft', () => {
    expect(EDGE_SHARE).toBe(1);
    expect(EDGE_POWER).toBe(3);
    const c = edgeLightColor([0, 0, 10], [0, 0, 1], tint);
    c.forEach((v, k) => expect(v).toBeCloseTo((tint[k]! * EDGE_SHARE) / Math.PI, 12));
  });

  it('fades as the star moves to the side and is gone beside or behind the camera', () => {
    const half = edgeLightColor([0, 0, 10], [Math.sin(Math.PI / 3), 0, Math.cos(Math.PI / 3)], tint);
    expect(half[0]).toBeCloseTo((0.5 * EDGE_SHARE) / Math.PI, 12);
    expect(edgeLightColor([0, 0, 10], [1, 0, 0], tint)).toEqual([0, 0, 0]);
    expect(edgeLightColor([0, 0, 10], [0, 0, -1], tint)).toEqual([0, 0, 0]);
  });
});

describe('the edge light on a model\'s materials', () => {
  it('patches each standard material once, sharing the uniforms, and leaves other materials as they are', () => {
    const uniforms = createEdgeLightUniforms();
    const standard = new THREE.MeshStandardMaterial();
    const physical = new THREE.MeshPhysicalMaterial();
    const basic = new THREE.MeshBasicMaterial();
    const model = new THREE.Group();
    model.add(new THREE.Mesh(new THREE.BoxGeometry(), standard), new THREE.Mesh(new THREE.BoxGeometry(), [physical, basic]), new THREE.Mesh(new THREE.BoxGeometry(), standard));
    expect(addEdgeLight(model, uniforms)).toBe(2);
    expect(addEdgeLight(model, uniforms)).toBe(0);
    expect(basic.onBeforeCompile.toString()).not.toContain('uEdgeColor');
    expect(standard.customProgramCacheKey()).toBe('craft-edge-light');
    expect(uniforms.uEdgePower.value).toBe(EDGE_POWER);
  });

  it('adds its light to three\'s own standard fragment just before the output, with its uniforms declared', () => {
    const uniforms = createEdgeLightUniforms();
    const material = new THREE.MeshStandardMaterial();
    const model = new THREE.Mesh(new THREE.BoxGeometry(), material);
    addEdgeLight(model, uniforms);
    const shader = { uniforms: {} as Record<string, unknown>, fragmentShader: THREE.ShaderLib.physical.fragmentShader, vertexShader: THREE.ShaderLib.physical.vertexShader };
    material.onBeforeCompile(shader as never, undefined as never);
    expect(shader.uniforms.uEdgeColor).toBe(uniforms.uEdgeColor);
    expect(shader.uniforms.uEdgePower).toBe(uniforms.uEdgePower);
    expect(shader.fragmentShader).toContain('uniform vec3 uEdgeColor;');
    expect(shader.fragmentShader.indexOf(EDGE_FRAGMENT)).toBeGreaterThan(0);
    expect(shader.fragmentShader.indexOf(EDGE_FRAGMENT)).toBeLessThan(shader.fragmentShader.indexOf('#include <opaque_fragment>'));
    // Three's standard fragment declares what the line reads
    expect(THREE.ShaderLib.physical.fragmentShader).toContain('varying vec3 vViewPosition;');
  });
});
