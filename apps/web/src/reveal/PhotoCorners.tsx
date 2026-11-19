import React from 'react';
import type { PhotoRect } from '../components/sourcePhotoRect.js';
import { cornerPaths } from './cornerPaths.js';

/** The viewfinder corners over the window, at the photo's exact frame. A drawing: hidden from screen readers. */
export const PhotoCorners: React.FC<{ rect: PhotoRect }> = ({ rect }) => (
  <svg className="photo-corners" aria-hidden="true" focusable="false">
    {cornerPaths(rect).map((d) => (
      <path key={d} d={d} />
    ))}
  </svg>
);
