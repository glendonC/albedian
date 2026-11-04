import type { WebGPURenderer } from 'three/webgpu';

interface ComputeNodeLike {
  isComputeNode?: boolean;
  count: unknown;
  dispatchSize: unknown;
}

type ComputeFn = (nodes: unknown, dispatchSize?: unknown) => unknown;

const patched = new WeakSet<WebGPURenderer>();

/**
 * Compatibility shim for @sparkjsdev/radix 0.0.1 on three r187.
 *
 * radix 0.0.1 was built against three 0.186.0. It creates compute nodes with
 * `.compute([1, 1, 1], workgroupSize)` and later calls `renderer.compute(node, size)`. three r187
 * (#34813) throws "A dispatch size cannot be passed to renderer.compute() for nodes created with
 * compute( count )". r187's WebGPU backend resolves `dispatchSize ?? node.dispatchSize ?? node.count`,
 * so moving the size onto `node.dispatchSize` keeps the r186 behavior. Remove this once radix
 * publishes a release that uses `computeKernel()`.
 */
export function applyRadixR187ComputeShim(renderer: WebGPURenderer): void {
  if (patched.has(renderer)) return;
  patched.add(renderer);
  const target = renderer as unknown as { compute: ComputeFn };
  const original = target.compute.bind(renderer) as ComputeFn;
  target.compute = (nodes: unknown, dispatchSize: unknown = null) => {
    if (dispatchSize !== null && dispatchSize !== undefined) {
      const list = (Array.isArray(nodes) ? nodes : [nodes]) as ComputeNodeLike[];
      if (list.some((n) => n.count !== null || n.dispatchSize !== null)) {
        for (const n of list) n.dispatchSize = dispatchSize;
        return original(nodes);
      }
    }
    return original(nodes, dispatchSize);
  };
}
