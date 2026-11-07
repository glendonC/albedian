---
inclusion: fileMatch
fileMatchPattern: "apps/web/**"
---

# Design System, Motion & Accessibility Steering

- **Color & Surface Tokens:** Use `--alb-space-0` (`#06090D`), `--alb-space-1` (`#10161D`), `--alb-surface-raised` (`#1B232B`), and categorical evidence accents (`#D1E9EF` observed, `#81C5CC` catalog_matched, `#D9AE75` predicted, `#ABB0D6` simulated, `#E6AA8C` warning) Always pair color with distinct marker geometry and text labels.
- **Typography:** Use tabular numerals (`font-variant-numeric: tabular-nums`) for all timestamps, celestial coordinates, pixel residuals, and distances.
- **Signature Transition:** Lock the selected star in screen space during Phase A (`Photo -> Registered Sky`) via off-axis frustum shift; introduce 3D parsec parallax only in Phase B (`Registered Sky -> 3D Space`) with the `Simulated` badge and `"Drawn or computed by the app; not measured in the source recording."` disclosure, where each layer names its own source and limits.
- **Reduced Motion & Keyboard Parity:** Respect `prefers-reduced-motion: reduce` by replacing camera travel with short crossfades, and maintain 100% keyboard/pointer/text parity for all voice actions.
