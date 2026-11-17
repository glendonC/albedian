export const PLANETARY_NOISE_AND_HEIGHT_GLSL = /* glsl */ `
  // Procedural hash and analytical-derivative value noise
  float hash2(vec2 p) {
    p = fract(p * vec2(127.1, 311.7));
    p += dot(p, p + 34.5);
    return fract(p.x * p.y);
  }

  // Returns vec3(value, df/dx, df/dz)
  vec3 noised(vec2 x) {
    vec2 i = floor(x);
    vec2 f = fract(x);
    vec2 u = f * f * f * (f * (f * 6.0 - 15.0) + 10.0);
    vec2 du = 30.0 * f * f * (f * (f - 2.0) + 1.0);

    float a = hash2(i + vec2(0.0, 0.0));
    float b = hash2(i + vec2(1.0, 0.0));
    float c = hash2(i + vec2(0.0, 1.0));
    float d = hash2(i + vec2(1.0, 1.0));

    float k0 = a;
    float k1 = b - a;
    float k2 = c - a;
    float k3 = a - b - c + d;

    return vec3(
      k0 + k1 * u.x + k2 * u.y + k3 * u.x * u.y,
      du * (vec2(k1, k2) + k3 * u.yx)
    );
  }

  const mat2 m2 = mat2(0.80, -0.60, 0.60, 0.80);

  // Multi-octave ridged canyon and crater heightfield (modelled composition)
  float terrainHeight(vec2 p) {
    vec2 pos = p * 0.085;
    float a = 0.0;
    float b = 1.0;
    vec2 d = vec2(0.0);

    float dist = length(p);
    float valleyChannel = smoothstep(2.2, 11.5, abs(p.x + sin(p.y * 0.08) * 4.5));
    float bankSmooth = smoothstep(0.0, 0.42, valleyChannel);

    for (int i = 0; i < 8; i++) {
      vec3 n = noised(pos);
      d += b * n.yz;
      float ridge = 1.0 - abs(n.x * 2.0 - 1.0);
      ridge = ridge * ridge;
      float octaveMask = (i >= 3) ? mix(0.12, 1.0, bankSmooth) : 1.0;
      a += b * octaveMask * mix(n.x, ridge, 0.52) / (1.0 + dot(d, d) * 0.85);
      b *= 0.49;
      pos = m2 * pos * 2.04;
    }

    float horizonWall = smoothstep(12.0, 58.0, dist) * (1.8 + 1.4 * valleyChannel);

    if (uPaletteId < 0.5) {
      float cDist = length(p - vec2(-2.0, -24.0));
      float craterRim = exp(-pow((cDist - 12.5) / 3.8, 2.0)) * 3.8;
      float craterBowl = cDist < 12.0 ? -1.8 * (1.0 - cDist / 12.0) : 0.0;
      return -2.4 + a * 2.4 + craterRim + craterBowl + horizonWall * 0.75;
    }

    float riverBed = -2.48 + 0.55 * valleyChannel;
    return riverBed + a * (2.65 + horizonWall * 1.55) * (0.08 + 0.92 * valleyChannel);
  }
`;
