export const STAR_MOUNT_NAMES = Object.freeze(['catalogStarField', 'catalogStarSprites']);
export const MAX_BODY_RADIUS = 500;

/** Runs before the app: Three.js announces every Scene and WebGLRenderer to this DevTools hook. */
export function installCapturePrelude(win) {
  win.__captureObserved = [];
  win.__THREE_DEVTOOLS__ = new EventTarget();
  win.__THREE_DEVTOOLS__.addEventListener('observe', (e) => win.__captureObserved.push(e.detail));
}

/**
 * Installs `window.__capture` helpers in the target page. Kept free of outer module closures so
 * `installCaptureHelpers.toString()` evaluates cleanly in headless Chrome via `Runtime.evaluate`.
 */
export function installCaptureHelpers(win, doc) {
  const isStarMount = (o) => o.name === 'catalogStarField' || o.name === 'catalogStarSprites';
  const hasStarMountAncestor = (o) => {
    for (let p = o; p; p = p.parent) {
      if (isStarMount(p)) return true;
    }
    return false;
  };

  win.__capture = {
    liveWebGl() {
      return win.__captureObserved
        .filter((o) => o.isWebGLRenderer && doc.body.contains(o.domElement))
        .pop();
    },
    scene() {
      if (win.__albedianCosmos) return win.__albedianCosmos.renderer.scene;
      let live = null;
      for (const o of win.__captureObserved) {
        if (!o.isScene) continue;
        let cameras = 0;
        o.traverse((c) => {
          if (c.isPerspectiveCamera) cameras++;
        });
        if (cameras > 0) live = o;
      }
      return live;
    },
    camera() {
      if (win.__albedianCosmos) return win.__albedianCosmos.renderer.camera;
      let cam = null;
      this.scene().traverse((c) => {
        if (c.isPerspectiveCamera && !cam) cam = c;
      });
      return cam;
    },
    shown(o) {
      for (let p = o; p; p = p.parent) {
        if (!p.visible) return false;
      }
      return true;
    },
    setStarsVisible(visible) {
      let n = 0;
      this.scene().traverse((o) => {
        if (isStarMount(o)) {
          o.visible = visible;
          n++;
        }
      });
      return n;
    },
    frames(n) {
      return new Promise((resolve) => {
        const step = () => (n-- <= 0 ? resolve() : requestAnimationFrame(step));
        requestAnimationFrame(step);
      });
    },
    async png(kind) {
      if (kind === 'webgpu') return win.__albedianCosmos.capturePngDataUrl();
      await this.frames(3);
      return this.liveWebGl().domElement.toDataURL('image/png');
    },
    async store(kind) {
      this.stored = await this.png(kind);
      const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(this.stored));
      const sha256 = Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
      return { length: this.stored.length, sha256 };
    },
    piece(start, end) {
      return this.stored.slice(start, end);
    },
    geometry(kind) {
      const cam = this.camera();
      const scene = this.scene();
      scene.updateMatrixWorld(true);
      const out = {
        kind,
        fov: cam.fov,
        aspect: cam.aspect,
        projectionMatrix: Array.from(cam.projectionMatrix.elements),
        matrixWorld: Array.from(cam.matrixWorld.elements),
      };
      let mount = null;
      scene.traverse((o) => {
        if (isStarMount(o) && !mount && this.shown(o)) mount = o;
      });
      out.starMountMatrixWorld = mount ? Array.from(mount.matrixWorld.elements) : null;
      out.bodies = [];
      scene.traverse((o) => {
        if (!o.isMesh || !this.shown(o) || !o.geometry || o.geometry.isInstancedBufferGeometry) return;
        if (hasStarMountAncestor(o)) return;
        if (!o.geometry.boundingSphere) o.geometry.computeBoundingSphere();
        const sphere = o.geometry.boundingSphere;
        const e = o.matrixWorld.elements;
        const scale = Math.max(
          Math.hypot(e[0], e[1], e[2]),
          Math.hypot(e[4], e[5], e[6]),
          Math.hypot(e[8], e[9], e[10]),
        );
        const radius = sphere.radius * scale;
        if (radius > 500) return;
        const c = sphere.center.clone().applyMatrix4(o.matrixWorld);
        out.bodies.push({
          name: o.name || o.geometry.type,
          center: [c.x, c.y, c.z],
          radius,
          matrixWorld: Array.from(e),
        });
      });
      return out;
    },
  };
  return true;
}

export const PRELUDE_SCRIPT = `(${installCapturePrelude.toString()})(window);\n`;
export const HELPERS_SCRIPT = `(${installCaptureHelpers.toString()})(window, document);\n`;
