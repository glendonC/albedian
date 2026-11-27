/**
 * sample-reveal-one (UIUX-sample-reveal-READY.md items 1 and 2, RULED at the lead's line stamped 03:48 on 27 Nov): on a
 * sample night the photo view's pill reads "Sample photo" in place of "Your photo", as Power BI's samples carry the word
 * in their own name, and "your photo" in anything a person reads names it "the sample photo"; the possessive never names
 * the sample. "Your view" in the flight stays: the camera flown is the person's.
 */
export interface PhotoNames {
  /** The photo view's pill and the keyboard help's first view. */
  view: 'Your photo' | 'Sample photo';
  /** The photo inside a sentence. */
  the: 'your photo' | 'the sample photo';
  /** The photo opening a sentence. */
  The: 'Your photo' | 'The sample photo';
  /** The photo's alternative text. */
  alt: 'Your photo as uploaded' | 'The sample photo';
}

export const OWN_PHOTO: PhotoNames = { view: 'Your photo', the: 'your photo', The: 'Your photo', alt: 'Your photo as uploaded' };
export const SAMPLE_PHOTO: PhotoNames = { view: 'Sample photo', the: 'the sample photo', The: 'The sample photo', alt: 'The sample photo' };

/** The photo's names: the sample's on a sample night, else the person's own. */
export function photoNames(sample: boolean): PhotoNames {
  return sample ? SAMPLE_PHOTO : OWN_PHOTO;
}
