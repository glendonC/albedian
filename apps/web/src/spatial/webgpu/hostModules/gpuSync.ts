import type { WebGPURenderer } from 'three/webgpu';
import type { BackendInternals, RendererInternals, WebGpuHostBackend } from './hostTypes.js';

const yieldChannel = typeof MessageChannel !== 'undefined' ? new MessageChannel() : null;
const yieldQueue: Array<() => void> = [];
if (yieldChannel) yieldChannel.port1.onmessage = () => yieldQueue.shift()?.();

/** Yields to the event loop without setTimeout's 4 ms nesting clamp. */
export function yieldTask(): Promise<void> {
  if (!yieldChannel) return new Promise((resolve) => setTimeout(resolve, 0));
  return new Promise((resolve) => {
    yieldQueue.push(resolve);
    yieldChannel.port2.postMessage(0);
  });
}

export function detectHostBackend(renderer: WebGPURenderer): WebGpuHostBackend {
  return (renderer.backend as unknown as BackendInternals).isWebGPUBackend ? 'webgpu' : 'webgl2';
}

/**
 * Resets per-frame renderer info when `autoReset` is enabled and advances three's node frame
 * for offscreen rendering outside `setAnimationLoop`.
 */
export function advanceRendererNodeFrame(renderer: WebGPURenderer): void {
  const internals = renderer as unknown as RendererInternals;
  if (!internals._nodes) throw new Error('WebGpuHost: renderer node manager is not initialized');
  if (internals.info.autoReset) internals.info.reset();
  internals._nodes.nodeFrame.update();
  internals.info.frame = internals._nodes.nodeFrame.frameId;
}

/**
 * Resolves when the GPU has finished all submitted work. WebGPU uses `queue.onSubmittedWorkDone()`;
 * WebGL2 polls a fence across tasks because Chrome's `gl.finish()` does not block.
 */
export async function waitForBackendGpuIdle(renderer: WebGPURenderer): Promise<void> {
  const backend = renderer.backend as unknown as BackendInternals;
  if (backend.isWebGPUBackend && backend.device) {
    await backend.device.queue.onSubmittedWorkDone();
    return;
  }
  const gl = backend.gl;
  if (!gl) return;
  const sync = gl.fenceSync(gl.SYNC_GPU_COMMANDS_COMPLETE, 0);
  if (!sync) return;
  gl.flush();
  for (;;) {
    const status = gl.clientWaitSync(sync, 0, 0);
    if (status === gl.ALREADY_SIGNALED || status === gl.CONDITION_SATISFIED || status === gl.WAIT_FAILED) break;
    await yieldTask();
  }
  gl.deleteSync(sync);
}
