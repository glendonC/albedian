import React, { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { DRACOLoader } from 'three/examples/jsm/loaders/DRACOLoader.js';
import dracoWrapperUrl from 'three/examples/jsm/libs/draco/gltf/draco_wasm_wrapper.js?url';
import dracoWasmUrl from 'three/examples/jsm/libs/draco/gltf/draco_decoder.wasm?url';
import type { Vec3, WcsCameraRows } from '@albedian/camera-rig';
import type { FlightCamera } from './flightFrame.js';
import type { Craft } from './crafts.js';
import { craftSceneOf, type CraftFraming } from './craftScene.js';

/**
 * pilot-1: a NASA model drawn in the photo's own view during the flight through the stars, a few metres ahead of the
 * camera at the framing point and turned toward the star flown to, lit from the Sun's real direction. Its own canvas
 * sits over the star layer. No words and no movement of its own: it moves only because the flight's camera does.
 * The light's strength is the app's choice so the craft can be seen (the design lane words what is said about it).
 */

type CraftState = 'loading' | 'drawn' | 'failed';

/** Loads a NASA model (Draco compressed as NASA publishes it), the decoder from three's own files as Vite serves them. */
function loadModel(url: string): Promise<THREE.Group> {
  const manager = new THREE.LoadingManager();
  manager.setURLModifier((asked) => (asked.endsWith('draco_wasm_wrapper.js') ? dracoWrapperUrl : asked.endsWith('draco_decoder.wasm') ? dracoWasmUrl : asked));
  const draco = new DRACOLoader(manager);
  draco.setDecoderPath('/draco/');
  const loader = new GLTFLoader(manager);
  loader.setDRACOLoader(draco);
  return loader.loadAsync(url).then((gltf) => {
    draco.dispose();
    return gltf.scene;
  });
}

interface CraftGpu {
  renderer: THREE.WebGLRenderer;
  scene: THREE.Scene;
  camera: THREE.Camera;
  holder: THREE.Group;
  sun: THREE.DirectionalLight;
  centre: [number, number, number];
  radius: number;
}

function drawCraft(gpu: CraftGpu, camera: FlightCamera, craft: Craft, steer?: { positionPc: Vec3; rows: WcsCameraRows; craftRows?: WcsCameraRows }, framing?: CraftFraming): boolean {
  const scene = craftSceneOf(camera, craft, gpu.centre, gpu.radius, steer, framing);
  if (!scene) return false;
  gpu.holder.matrix.fromArray(scene.modelMatrix);
  gpu.holder.matrixWorldNeedsUpdate = true;
  gpu.camera.projectionMatrix.fromArray(scene.clipMatrix);
  gpu.camera.projectionMatrixInverse.copy(gpu.camera.projectionMatrix).invert();
  // The light shines from the Sun's side onto the craft
  const c = new THREE.Vector3(scene.modelMatrix[12], scene.modelMatrix[13], scene.modelMatrix[14]);
  gpu.sun.position.copy(c).addScaledVector(new THREE.Vector3(...scene.toSun), 10 * gpu.radius + 1);
  gpu.sun.target.position.copy(c);
  gpu.sun.target.updateMatrixWorld();
  gpu.renderer.render(gpu.scene, gpu.camera);
  return true;
}

export const FlightCraftLayer: React.FC<{
  camera: FlightCamera;
  craft: Craft;
  /** Loads the model; NASA's file by default. */
  load?: (url: string) => Promise<THREE.Group>;
  /** While the person steers: the camera they turn and move. */
  steer?: { positionPc: Vec3; rows: WcsCameraRows; craftRows?: WcsCameraRows } | null;
  /** Where the screen frames the craft; the laptop's (D26 item 2) by default. */
  framing?: CraftFraming;
}> = ({ camera, craft, load = loadModel, steer = null, framing }) => {
  const canvas = useRef<HTMLCanvasElement>(null);
  const gpu = useRef<CraftGpu | null>(null);
  const cameraRef = useRef(camera);
  cameraRef.current = camera;
  const steerRef = useRef(steer);
  steerRef.current = steer;
  const framingRef = useRef(framing);
  framingRef.current = framing;
  const [state, setState] = useState<CraftState>('loading');
  const dpr = typeof window === 'undefined' ? 1 : Math.min(2, window.devicePixelRatio || 1);

  useEffect(() => {
    let current = true;
    const el = canvas.current;
    if (!el) return undefined;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ canvas: el, alpha: true, antialias: true, premultipliedAlpha: true });
    } catch {
      setState('failed');
      return undefined;
    }
    renderer.setPixelRatio(dpr);
    renderer.setSize(camera.viewportWidthPx, camera.viewportHeightPx, false);
    renderer.setClearColor(0x000000, 0);
    const scene = new THREE.Scene();
    const cam = new THREE.Camera();
    cam.matrixAutoUpdate = false;
    const holder = new THREE.Group();
    holder.matrixAutoUpdate = false;
    scene.add(holder);
    const sun = new THREE.DirectionalLight(0xffffff, 3);
    scene.add(sun, sun.target);
    void load(craft.modelUrl).then(
      (model) => {
        if (!current) return;
        model.traverse((o) => {
          o.frustumCulled = false;
        });
        const box = new THREE.Box3().setFromObject(model);
        const sphere = box.getBoundingSphere(new THREE.Sphere());
        holder.add(model);
        gpu.current = { renderer, scene, camera: cam, holder, sun, centre: [sphere.center.x, sphere.center.y, sphere.center.z], radius: sphere.radius };
        setState(drawCraft(gpu.current, cameraRef.current, craft, steerRef.current ?? undefined, framingRef.current) ? 'drawn' : 'failed');
      },
      () => {
        if (current) setState('failed');
      }
    );
    return () => {
      current = false;
      gpu.current = null;
      renderer.dispose();
    };
  }, [craft, load, dpr]);

  // Every frame of the flight draws the craft again from where the camera stands
  useEffect(() => {
    const g = gpu.current;
    if (!g) return;
    g.renderer.setSize(camera.viewportWidthPx, camera.viewportHeightPx, false);
    drawCraft(g, camera, craft, steer ?? undefined, framing);
  }, [camera, craft, steer, framing?.xPx, framing?.yPx, framing?.diameterPx]);

  return (
    <div className="flight-craft" data-testid="flight-craft" data-craft={craft.id} data-state={state} aria-hidden="true" style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}>
      <canvas ref={canvas} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', display: 'block' }} />
    </div>
  );
};
