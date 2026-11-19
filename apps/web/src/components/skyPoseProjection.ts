import type { CameraSkyPose } from '../spatial/cosmosRenderer.js';
import { conjugateQuat, listenerOrientationIcrs } from '../audio/skySourceModules/icrsQuaternion.js';
import { rotateByQuat, type Vec3 } from '../audio/voicePlanning.js';

/**
 * Where an ICRS direction lands on screen in the sky the renderer draws now: turned into the camera's axes through the
 * renderer's own camera and star sphere (its observer frame and its look, CosmosRenderer.getCameraSkyPose), then
 * through its frustum. None for a direction behind the camera or a pose that describes no view.
 */
export function projectIcrsThroughPose(
  direction: Vec3,
  pose: CameraSkyPose,
  viewportWidthPx: number,
  viewportHeightPx: number
): { visible: boolean; xPx: number; yPx: number } | null {
  const orientation = listenerOrientationIcrs(pose);
  if (!orientation || !(pose.fovDeg > 0 && pose.fovDeg < 180) || !(pose.aspect > 0)) return null;
  const [x, y, z] = rotateByQuat(direction, conjugateQuat(orientation));
  const depth = -z;
  if (!(depth > 0)) return { visible: false, xPx: 0, yPx: 0 };
  const tanY = Math.tan((pose.fovDeg * Math.PI) / 360);
  const ndcX = x / (depth * tanY * pose.aspect);
  const ndcY = y / (depth * tanY);
  return {
    visible: Math.abs(ndcX) <= 1 && Math.abs(ndcY) <= 1,
    xPx: ((ndcX + 1) / 2) * viewportWidthPx,
    yPx: ((1 - ndcY) / 2) * viewportHeightPx,
  };
}
