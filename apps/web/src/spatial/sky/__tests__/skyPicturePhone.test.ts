import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LANDSCAPE_PHONE_QUERY, PHONE_QUERY } from '../../../components/phoneSheet.js';
import { SKY_PICTURE_PHONE_COPIES, forgetKeptSkyPictures, keptSkyPicture, skyPictureSourceOf } from '../skyPicture.js';
import { installSkyFakes } from './skyPictureFakes.js';
import { smallCraftSky } from '../../flight/FlightCraftLayer.js';

// sky-picture-phone-size-one (DECIDED-BY-LEAD 21:56): on a phone the loader keeps a half-side copy of each sky picture,
// made by ASSETS and recorded with its source, so the kept pictures take a quarter of the memory there.

const PANORAMA = '/textures/eso_milkyway_panorama.jpg';
const GLOW = '/textures/eso_milkyway_diffuse_6k.webp';

beforeEach(() => forgetKeptSkyPictures());
afterEach(() => {
  vi.unstubAllGlobals();
  forgetKeptSkyPictures();
});

const asPhone = (phone: boolean) => vi.stubGlobal('matchMedia', (query: string) => ({ matches: phone && query === PHONE_QUERY, addEventListener: () => undefined, removeEventListener: () => undefined }));

describe('the sky pictures a phone keeps', () => {
  it('names the half-side copy of the panorama and of the starless glow', () => {
    expect(SKY_PICTURE_PHONE_COPIES).toEqual({ [PANORAMA]: '/textures/eso_milkyway_panorama_3k.jpg', [GLOW]: '/textures/eso_milkyway_diffuse_3k.webp' });
  });

  it('takes the copy on a phone and the full picture elsewhere, decided once a page', () => {
    asPhone(true);
    expect(skyPictureSourceOf(PANORAMA)).toBe('/textures/eso_milkyway_panorama_3k.jpg');
    expect(skyPictureSourceOf(GLOW)).toBe('/textures/eso_milkyway_diffuse_3k.webp');
    expect(skyPictureSourceOf('/textures/other.jpg')).toBe('/textures/other.jpg');
    // A window that leaves the phone's width later keeps what the page began with
    asPhone(false);
    expect(skyPictureSourceOf(PANORAMA)).toBe('/textures/eso_milkyway_panorama_3k.jpg');
    forgetKeptSkyPictures();
    expect(skyPictureSourceOf(PANORAMA)).toBe(PANORAMA);
  });

  it('takes the copy on a phone held sideways too', () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query === LANDSCAPE_PHONE_QUERY, addEventListener: () => undefined, removeEventListener: () => undefined }));
    expect(skyPictureSourceOf(GLOW)).toBe('/textures/eso_milkyway_diffuse_3k.webp');
  });

  it('fetches the copy on a phone and keeps it under the picture\'s own address', async () => {
    asPhone(true);
    const fakes = installSkyFakes({ pictureUrl: '/textures/eso_milkyway_panorama_3k.jpg', pictureRows: 200 });
    const asked = keptSkyPicture(PANORAMA);
    let kept: unknown;
    void asked.then((p) => (kept = p));
    for (let i = 0; i < 10 && kept === undefined; i++) await fakes.frame();
    expect(kept).not.toBeNull();
    expect(fakes.fetched.filter((u) => u.startsWith('/textures/'))).toEqual(['/textures/eso_milkyway_panorama_3k.jpg']);
    expect(await keptSkyPicture(PANORAMA)).toBe(kept);
  });

  it('shrinks the craft\'s sky from the glow\'s copy on a phone, so the page fetches one glow there, and from the glow elsewhere', async () => {
    for (const [phone, want] of [[true, '/textures/eso_milkyway_diffuse_3k.webp'], [false, GLOW]] as const) {
      forgetKeptSkyPictures();
      asPhone(phone);
      const fakes = installSkyFakes({ pictureUrl: want });
      await smallCraftSky();
      expect(fakes.fetched.filter((u) => u.startsWith('/textures/'))).toEqual([want]);
    }
  });
});
