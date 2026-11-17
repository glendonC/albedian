import type { Node, Texture } from 'three/webgpu';

export type FloatNode = Node<'float'>;
export type Vec2Node = Node<'vec2'>;
export type Vec3Node = Node<'vec3'>;
export type Vec4Node = Node<'vec4'>;

export interface EarthTextures {
  /** NASA Blue Marble Next Generation, March 2004 base map without relief shading (sRGB) */
  day: Texture;
  /** NASA Black Marble 2016 city lights (sRGB) */
  night: Texture;
  /** Natural Earth 1:10m ocean and lakes water fraction (linear) */
  water: Texture;
  /** NOAA ETOPO 2022 surface elevation encoded as round(h + 11000) in R and G (linear, NearestFilter) */
  height: Texture;
  /** NASA Blue Marble 2002 cloud layer (sRGB, red channel used) */
  clouds: Texture;
}

export interface MoonTextures {
  /** NASA SVS 4720 colour from the LROC WAC mosaic (sRGB); longitude -180 at the left edge */
  colour: Texture;
  /** Tangent-space normals (east, north, up) from the LOLA heights (linear data), same layout */
  normal: Texture;
}
