// first-paint-one (UIUX-first-paint-READY.md P2 and P5, DECIDED-BY-LEAD 20:44 on 27 Nov): the page's first frame shows the
// home's logo only once Geist 600 has loaded, never in a fallback face, and only at the home's address: an address naming
// one upload (openUpload.ts uploadAddressed: one upload value of 64 lowercase hex digits) opens on the wait, so its first
// frame is the home's black alone. A classic script from the app's own origin, since the content policy allows no inline
// script; it runs after the page's stylesheets, before the app's own script.
(function () {
  var frame = document.getElementById('first-frame');
  if (!frame) return;
  var query = new URLSearchParams(window.location.search);
  var uploads = query.getAll('upload');
  if (uploads.length === 1 && /^[0-9a-f]{64}$/.test(uploads[0])) return;
  // A sample night's address (App.tsx addressedSample: one sample value matching the contract's SAMPLE_ID_PATTERN, where
  // no upload is named) opens behind the same black, so it keeps the logo out too (AUDIT-C's note on version two)
  var samples = query.getAll('sample');
  if (samples.length === 1 && /^night-[a-z0-9-]{1,40}$/.test(samples[0])) return;
  var fonts = document.fonts;
  if (!fonts || typeof fonts.load !== 'function') return;
  fonts.load('600 132px Geist').then(
    function (faces) {
      if (faces && faces.length > 0) frame.setAttribute('data-logo', '');
    },
    function () {}
  );
})();
