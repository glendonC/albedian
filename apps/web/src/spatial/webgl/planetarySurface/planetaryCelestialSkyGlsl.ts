export const PLANETARY_CELESTIAL_SKY_GLSL = /* glsl */ `
  // Evaluates the modelled sky and primary celestial body along ray direction rd
  vec3 evaluateCelestialSky(vec3 rd, bool isReflection) {
    float elev = clamp(rd.y, -0.15, 1.0);
    float horizonGlow = exp(-abs(rd.y - 0.01) * 6.5);
    vec3 primaryDir = normalize(vec3(-0.18, 0.22, -0.958));
    vec4 brunetonSky = sampleBrunetonAtmoLut(uBrunetonLutTex, elev, dot(rd, primaryDir) * 0.38);
    vec3 baseSky = mix(uAtmoHorizon * 0.78, uAtmoZenith, smoothstep(-0.02, 0.52, elev));
    baseSky += uAtmoHorizon * horizonGlow * 0.65;
    vec3 sky = (uPaletteId < 0.5)
      ? baseSky
      : mix(baseSky, baseSky * 0.72 + brunetonSky.rgb * uAtmoHorizon * 0.85, 0.42);

    if (uPaletteId < 0.5) {
      // Palette 0: modelled Earth sphere in the lunar sky
      float b = dot(rd, primaryDir);
      float rEarth = 0.145;
      float disc = b * b - (1.0 - rEarth * rEarth);
      float limbGlow = exp(-max(0.0, acos(clamp(b, -1.0, 1.0)) - rEarth * 0.92) * 32.0);
      sky += vec3(0.18, 0.48, 0.96) * limbGlow * 0.55;

      if (disc > 0.0 && b > 0.0) {
        float tHit = b - sqrt(disc);
        vec3 hitPos = rd * tHit - primaryDir;
        vec3 N = normalize(hitPos);
        vec2 uv = vec2(atan(N.x, N.z) / 6.28318 + 0.5 + uTime * 0.0015, asin(clamp(N.y, -1.0, 1.0)) / 3.14159 + 0.5);

        vec3 daySample = texture2D(uEarthDayTex, uv).rgb;
        vec3 dayCol = pow(daySample, vec3(1.15));
        vec3 nightCol = texture2D(uEarthNightTex, uv).rgb;
        vec3 clouds = texture2D(uEarthCloudsTex, uv + vec2(uTime * 0.002, 0.0)).rgb;
        float waterMask = texture2D(uEarthSpecTex, uv).r;
        float specMask = waterMask * (1.0 - smoothstep(0.15, 0.35, min(daySample.r, daySample.g)));

        vec3 sunL = normalize(vec3(0.78, 0.32, 0.54));
        float ndl = dot(N, sunL);
        float dayFactor = smoothstep(-0.12, 0.22, ndl);
        float twilight = exp(-pow(ndl * 4.5, 2.0)) * 0.55;
        vec3 H = normalize(sunL - rd);
        float oceanSpec = pow(max(0.0, dot(N, H)), 48.0) * specMask * max(0.0, ndl);

        vec3 lit = dayCol * max(0.04, ndl * 1.2) + vec3(0.45, 0.72, 1.0) * oceanSpec * 0.75;
        lit = mix(lit, vec3(0.96, 0.98, 1.0) * max(0.1, ndl * 1.25), clouds.r * 0.74);
        vec3 cities = nightCol * vec3(1.3, 0.9, 0.45) * (1.0 - smoothstep(-0.18, 0.06, ndl)) * 1.35;
        vec3 earthSurf = mix(cities, lit, dayFactor) + vec3(0.95, 0.42, 0.16) * twilight;
        float mu = clamp(dot(N, -rd), 0.0, 1.0);
        earthSurf += vec3(0.24, 0.62, 1.0) * pow(1.0 - mu, 2.6) * (0.35 + 0.65 * dayFactor);
        return earthSurf;
      }
      return sky;
    }

    if (uPaletteId < 1.5) {
      // Palette 1: modelled red dwarf disk and bright double-star pair
      float cosAng = dot(rd, primaryDir);
      float ang = acos(clamp(cosAng, -1.0, 1.0));
      float rStar = 0.105;
      float corona = exp(-max(0.0, ang - rStar * 0.75) * 14.0);
      float godRays = (0.65 + 0.35 * sin(atan(rd.y - primaryDir.y, rd.x - primaryDir.x) * 14.0 + uTime * 0.2)) * exp(-ang * 5.5);
      sky += vec3(1.25, 0.38, 0.12) * corona * 0.68 + vec3(1.05, 0.45, 0.18) * godRays * 0.28;

      if (ang < rStar) {
        float mu = sqrt(max(0.0, 1.0 - pow(ang / rStar, 2.0)));
        vec2 granUv = (rd.xy - primaryDir.xy) * 32.0;
        float gran = noised(granUv + vec2(uTime * 0.15, -uTime * 0.1)).x;
        vec3 photosphere = mix(vec3(0.55, 0.06, 0.01), vec3(1.45, 0.48, 0.12), gran * 0.55 + mu * 0.45);
        photosphere = mix(photosphere, vec3(1.75, 1.15, 0.55), pow(mu, 1.6) * 0.65);
        return photosphere * (0.42 + 0.58 * pow(mu, 0.45));
      }

      vec3 cenA = normalize(vec3(0.28, 0.34, -0.897));
      vec3 cenB = normalize(vec3(0.305, 0.332, -0.892));
      float dA = acos(clamp(dot(rd, cenA), -1.0, 1.0));
      float dB = acos(clamp(dot(rd, cenB), -1.0, 1.0));
      sky += vec3(1.4, 1.32, 1.05) * exp(-dA * 180.0) * 1.4 + vec3(1.35, 0.92, 0.55) * exp(-dB * 210.0) * 1.1;
      return sky;
    }

    if (uPaletteId < 2.5) {
      // Palette 2: modelled ringed Saturn sphere and ring plane intersection
      vec3 satCenter = normalize(vec3(-0.14, 0.25, -0.958));
      float rSat = 0.135;
      float b = dot(rd, satCenter);
      float disc = b * b - (1.0 - rSat * rSat);

      vec3 ringN = normalize(vec3(0.32, 0.84, 0.44));
      float denom = dot(rd, ringN);
      vec3 ringCol = vec3(0.0);
      float ringAlpha = 0.0;
      float tRing = 1e5;
      if (abs(denom) > 0.001) {
        tRing = dot(satCenter, ringN) / denom;
        if (tRing > 0.0) {
          vec3 rHit = rd * tRing - satCenter;
          float rDist = length(rHit) / rSat;
          if (isReflection) {
            float softBand = exp(-pow((rDist - 1.86) / 0.52, 2.0));
            ringCol = vec3(0.95, 0.78, 0.48) * softBand;
            ringAlpha = softBand * 0.48;
          } else if (rDist > 1.28 && rDist < 2.45) {
            float cassini = smoothstep(0.015, 0.045, abs(rDist - 1.95));
            float encke = smoothstep(0.008, 0.022, abs(rDist - 2.26));
            float ringlets = 0.62 + 0.38 * sin(rDist * 140.0 + sin(rDist * 38.0) * 2.0);
            vec3 sunDir = normalize(vec3(0.68, 0.28, 0.67));
            float inShadow = (dot(rHit, sunDir) < 0.0 && length(cross(rHit, sunDir)) < rSat * 0.96) ? 0.12 : 1.0;
            ringCol = mix(vec3(0.68, 0.56, 0.38), vec3(1.08, 0.92, 0.68), ringlets) * cassini * encke * inShadow;
            ringAlpha = clamp(ringlets * cassini * encke * 0.88, 0.0, 0.92);
          }
        }
      }

      if (isReflection) {
        float angSat = acos(clamp(b, -1.0, 1.0));
        float satGlow = exp(-max(0.0, angSat - rSat * 0.4) * 12.0);
        vec3 reflSat = mix(sky, vec3(0.96, 0.78, 0.46), clamp(satGlow * 0.65 + ringAlpha * 0.55, 0.0, 0.85));
        return reflSat;
      }

      if (disc > 0.0 && b > 0.0) {
        float tSphere = b - sqrt(disc);
        vec3 hitPos = rd * tSphere - satCenter;
        vec3 N = normalize(hitPos);
        vec3 sunDir = normalize(vec3(0.68, 0.28, 0.67));
        float ndl = max(0.06, dot(N, sunDir));
        float lat = dot(N, ringN);
        float belts = 0.5 + 0.5 * sin(lat * 28.0 + sin(lat * 9.0) * 1.5);
        vec3 satSurf = mix(vec3(0.72, 0.58, 0.36), vec3(1.02, 0.86, 0.58), belts) * ndl * 1.25;
        if (ringAlpha > 0.0 && tRing < tSphere) {
          satSurf = mix(satSurf, ringCol, ringAlpha);
        }
        return mix(sky, satSurf, 0.92);
      }
      if (ringAlpha > 0.0) {
        return mix(sky, ringCol, ringAlpha * 0.88);
      }
      return sky;
    }

    return sky;
  }
`;
