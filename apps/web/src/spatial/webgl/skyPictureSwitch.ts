import * as THREE from 'three';
import { SKY_PICTURE_URL, type SkyPicture } from '../sky/predictedSkyPicture.js';
import { decodeSkyPicture, skyPictureSourceOf } from '../sky/skyPicture.js';
import type { SkyDomeUniforms } from './skyDomeAndCinemaShader.js';

export type { SkyPicture };

/** Each dome's panorama, once its registration is bound and it is on the dome (none if that fails). */
const panoramas = new WeakMap<SkyDomeUniforms, Promise<THREE.Texture | null>>();

/** Kept by loadSkyViewTextures (skyDomeAndCinemaShader.ts) for the dome it binds. */
export function recordPanorama(uniforms: SkyDomeUniforms, bound: Promise<THREE.Texture | null>): void {
  panoramas.set(uniforms, bound);
}

/** Puts a sky picture into a texture the dome can sample. */
export type SkyPictureUpload = (url: string) => Promise<THREE.Texture>;

const nextFrame = () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));

/**
 * The starless glow into the portal's own context (lead 21:58: a sky picture comes from SPATIAL's loader): decoded off
 * the main thread, rows flipped, by the loader's decodeSkyPicture (sky/skyPicture.ts), then uploaded a slice a frame
 * and its mipmaps on the frame after, as the loader's uploadSkyPicture does, but through three's own record of which
 * texture is bound so its state stays true, and stored as sRGB as the panorama's texture is (the dome grades linear
 * light). Wraps across, clamped at the poles. None where the portal has no renderer.
 */
export function rendererUpload(renderer: THREE.WebGLRenderer | null): SkyPictureUpload | null {
  if (!renderer) return null;
  return async (url) => {
    // On a phone the glow's half-side copy, as every other layer keeps there (sky-picture-phone-size-one)
    const picture = await decodeSkyPicture(skyPictureSourceOf(url));
    const gl = renderer.getContext() as WebGL2RenderingContext;
    const texture = gl.createTexture();
    let at = 0;
    try {
      if (!texture) throw new Error('sky picture: no texture');
      const bind = () => renderer.state.bindTexture(gl.TEXTURE_2D, texture);
      bind();
      gl.texStorage2D(gl.TEXTURE_2D, Math.floor(Math.log2(Math.max(picture.width, picture.height))) + 1, gl.SRGB8_ALPHA8, picture.width, picture.height);
      for (; at < picture.slices.length; at++) {
        await nextFrame();
        if (gl.isContextLost()) throw new Error('sky picture: context lost');
        const slice = picture.slices[at]!;
        bind();
        gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, slice.y, gl.RGBA, gl.UNSIGNED_BYTE, slice.bitmap);
        slice.bitmap.close();
      }
      await nextFrame();
      if (gl.isContextLost()) throw new Error('sky picture: context lost');
      bind();
      gl.generateMipmap(gl.TEXTURE_2D);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      return new THREE.ExternalTexture(texture);
    } finally {
      for (; at < picture.slices.length; at++) picture.slices[at]!.bitmap.close();
    }
  };
}

/** The catalog star field's limit, as SparkPortalRenderer's field has it. */
export interface LimitField {
  getLimitingMagnitude(): number;
  setLimitingMagnitude(mag: number): void;
}

/** Told which picture the dome shows once it shows it: the page words its line from this, never from what was asked. */
export type SkyPictureShown = (shown: SkyPicture) => void;

/**
 * predsky-starless-one (DECIDED-BY-LEAD 24 Nov 18:04 and 18:14, version two at 21:51): one renderer's sky dome picture
 * and catalog limit. The starless glow is loaded the first time it is asked for and kept, as the panorama is. It goes on
 * the dome only once the panorama's registration is bound (the glow is in the same projection) and only while still
 * asked for, and the deeper catalog limit and the planets' fill come with it, at the same moment: until then, and if
 * the glow never comes, the dome keeps the photograph at the limit it had, so no star is drawn twice, and the page is
 * told the photograph is what shows. The music's sky feed keeps hearing the limit the field had before the raise.
 * Like the panorama, the glow lives as long as the renderer's context.
 */
class SkyPictureSwitch {
  private picture: SkyPicture = 'photograph';
  private starless: Promise<THREE.Texture> | null = null;
  private heard: number | null = null;

  constructor(
    private readonly upload: SkyPictureUpload | null,
    private readonly uniforms: SkyDomeUniforms
  ) {}

  set(picture: SkyPicture, limitMag: number, field: LimitField | null, onShown?: SkyPictureShown): void {
    this.picture = picture;
    const placed = panoramas.get(this.uniforms) ?? Promise.resolve(null);
    if (picture === 'photograph') {
      // Back at once: the limit and the planets' fill; the panorama on the dome once it is bound
      if (field) field.setLimitingMagnitude(limitMag);
      this.heard = null;
      this.uniforms.uFillPlanets.value = 0;
      void placed.then((photograph) => {
        if (this.picture !== 'photograph') return;
        if (photograph) this.uniforms.uMilkyWayTex.value = photograph;
        onShown?.('photograph');
      });
      return;
    }
    const glow = (this.starless ??= this.upload ? this.upload(SKY_PICTURE_URL.starless) : Promise.reject(new Error('sky picture: no renderer')));
    void Promise.all([placed, glow]).then(
      ([photograph, tex]) => {
        if (this.picture !== 'starless') return;
        // A panorama that could not be placed gives the glow no place either
        if (!photograph) return onShown?.('photograph');
        this.uniforms.uMilkyWayTex.value = tex;
        this.uniforms.uFillPlanets.value = 1;
        if (field) {
          this.heard ??= field.getLimitingMagnitude();
          field.setLimitingMagnitude(limitMag);
        }
        onShown?.('starless');
      },
      // A glow that does not come leaves the photograph and its limit, says so, and is asked for again next time
      () => {
        this.starless = null;
        if (this.picture === 'starless') onShown?.('photograph');
      }
    );
  }

  heardLimit(field: LimitField): number {
    return this.heard ?? field.getLimitingMagnitude();
  }
}

const switches = new WeakMap<object, SkyPictureSwitch>();

/** The renderer's dome picture and catalog limit set (the predicted sky: the starless glow to 10.5). */
export function setSkyPictureOf(renderer: object, upload: SkyPictureUpload | null, uniforms: SkyDomeUniforms, field: LimitField | null, picture: SkyPicture, limitMag: number, onShown?: SkyPictureShown): void {
  let s = switches.get(renderer);
  if (!s) switches.set(renderer, (s = new SkyPictureSwitch(upload, uniforms)));
  s.set(picture, limitMag, field, onShown);
}

/** The limit the renderer's music hears: the field's own, unless the predicted sky raised it, then the one before. */
export function heardLimitOf(renderer: object, field: LimitField): number {
  return switches.get(renderer)?.heardLimit(field) ?? field.getLimitingMagnitude();
}
