/**
 * The home's backdrop: the Milky Way panorama the app already ships (ESO/S. Brunier, eso0932a, CC BY 4.0, recorded
 * in apps/web/public/textures/PROVENANCE.json), framed to the region the approved home mockup shows. No new file.
 *
 * The region was found by matching the mockup's 2880 by 1800 crop against the shipped 6000 by 3000 panorama
 * (normalised cross-correlation 0.964, .lanetools/arrive-home/match_crop.py in the WEB lane): the crop is 0.640 of the
 * panorama's width, its left edge at 0.3164 of the width and its top at 0.0879 of the height. The frame keeps the
 * crop's 1.6 shape and covers the window as the mockup's background does (home.css, .home-sky).
 */
export interface HomeBackdrop {
  url: string;
  /** The crop's width as a fraction of the panorama's width. */
  widthFraction: number;
  /** The crop's left edge, as a fraction of the panorama's width. */
  left: number;
  /** The crop's top edge, as a fraction of the panorama's height. */
  top: number;
  /** The crop's width over its height. */
  aspect: number;
}

export const HOME_BACKDROP: Readonly<HomeBackdrop> = {
  url: '/textures/eso_milkyway_panorama.jpg',
  widthFraction: 0.64,
  left: 0.3164,
  top: 0.0879,
  aspect: 1.6,
};

/**
 * The shade over the backdrop, darker toward the bottom where the logo and Upload sit, as the mockup draws it: each
 * stop's darkness (the opacity of its black) and its height above the bottom, in percent of the window.
 */
export const HOME_SHADE: ReadonlyArray<readonly [darkness: number, abovePercent: number]> = [
  [0.82, 0],
  [0.2, 46],
  [0, 70],
];

/** CSS for the shade, from its stops. */
export function homeShadeStyle(shade: ReadonlyArray<readonly [number, number]> = HOME_SHADE): { backgroundImage: string } {
  return { backgroundImage: `linear-gradient(to top, ${shade.map(([dark, above]) => `rgba(0, 0, 0, ${dark}) ${above}%`).join(', ')})` };
}

/**
 * What the home does to the panorama, for its credit (ESO's terms ask that modifications be indicated), worded from the
 * numbers that crop and shade it. The wording is [proposed]; the numbers are the home's own.
 */
export function homeBackdropChanges(
  backdrop: Readonly<HomeBackdrop> = HOME_BACKDROP,
  shade: ReadonlyArray<readonly [number, number]> = HOME_SHADE
): string {
  const width = Math.round(backdrop.widthFraction * 100);
  // The frame keeps its shape and covers the window, so its height follows from its width, its shape and the
  // panorama's own 2 to 1; a window of another shape than the frame's shows less of the width or of the height
  const height = Math.round(((backdrop.widthFraction * PANORAMA_ASPECT) / backdrop.aspect) * 100);
  const darkest = Math.round(Math.max(...shade.map(([dark]) => dark)) * 100);
  return (
    `On the home a part of it is shown, at most ${width} percent of its width and ${height} percent of its height ` +
    `(a window of another shape than ${backdrop.aspect} shows less of one of them), darkened toward the bottom, up to ` +
    `${darkest} percent black.`
  );
}

/** The panorama's own width over its height (plate carree, 360 by 180 degrees). */
const PANORAMA_ASPECT = 2;

const percent = (fraction: number) => `${Number((fraction * 100).toFixed(2))}%`;

/**
 * CSS for a frame the crop's shape: the panorama scaled so the crop spans the frame's width, then shifted so the
 * crop's corner sits on the frame's. A background position of p places p of the spare length before the image.
 */
export function homeBackdropStyle(backdrop: Readonly<HomeBackdrop> = HOME_BACKDROP): {
  backgroundImage: string;
  backgroundSize: string;
  backgroundPosition: string;
} {
  const scale = 1 / backdrop.widthFraction;
  // In frame widths: the panorama's width and height, and the frame's height
  const panoramaWidth = scale;
  const panoramaHeight = scale / PANORAMA_ASPECT;
  const frameHeight = 1 / backdrop.aspect;
  const x = (backdrop.left * panoramaWidth) / (panoramaWidth - 1);
  const y = (backdrop.top * panoramaHeight) / (panoramaHeight - frameHeight);
  return {
    backgroundImage: `url("${backdrop.url}")`,
    backgroundSize: `${percent(scale)} auto`,
    backgroundPosition: `${percent(x)} ${percent(y)}`,
  };
}
