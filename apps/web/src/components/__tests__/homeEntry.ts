import { act } from 'react';

// The one way past the home, as a person takes it: choosing a photo with the home's own file picker. The nine tests
// that used to press Launch take this step instead, so each changes its entry and nothing else.

/** Where the home is on screen, for a test that checks it is there or gone. */
export const HOME_SCREEN = '[data-testid="home-screen"]';
const HOME_FILE_INPUT = '[data-testid="home-file-input"]';

/** Chooses a photo on the home, which leaves it at once. Throws when no home is on screen. */
export function choosePhotoOnHome(container: ParentNode): File {
  const input = container.querySelector<HTMLInputElement>(HOME_FILE_INPUT);
  if (!input) throw new Error('No home on screen: there is no photo to choose.');
  const file = new File([new Uint8Array([0xff, 0xd8, 0xff, 0xd9])], 'sample.jpg', { type: 'image/jpeg' });
  Object.defineProperty(input, 'files', { value: [file], configurable: true });
  act(() => {
    input.dispatchEvent(new Event('change', { bubbles: true }));
  });
  return file;
}
