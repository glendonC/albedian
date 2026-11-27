import { describe, expect, it, vi } from 'vitest';
import * as THREE from 'three';

// craft-alive-two: the flight's own model loader keeps a file's animations on the scene it returns, where posesOf finds
// the suit's three poses (three's GLTFLoader returns them beside the scene, not on it).

const loaded = { scene: new THREE.Group(), animations: [new THREE.AnimationClip('hover', 0, []), new THREE.AnimationClip('cruise', 0, []), new THREE.AnimationClip('brake', 0, [])] };

vi.mock('three/examples/jsm/loaders/GLTFLoader.js', () => ({
  GLTFLoader: class {
    setDRACOLoader() {}
    loadAsync() {
      return Promise.resolve(loaded);
    }
  },
}));
vi.mock('three/examples/jsm/loaders/DRACOLoader.js', () => ({
  DRACOLoader: class {
    setDecoderPath() {}
    dispose() {}
  },
}));

describe('the flight\'s model loader', () => {
  it('returns the scene with the file\'s animations on it', async () => {
    const { createModelLoader } = await import('../FlightCraftLayer.js');
    const loader = createModelLoader();
    const scene = await loader.load('/models/hangar/emu/emu_lod2.glb');
    expect(scene).toBe(loaded.scene);
    expect(scene.animations.map((c) => c.name)).toEqual(['hover', 'cruise', 'brake']);
    loader.dispose();
  });
});
