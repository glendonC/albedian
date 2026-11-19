/**
 * Runs a vertex shader's own `main()` text on the CPU, for tests: the subset of GLSL ES 3.00 the flight's vertex
 * shader is written in (declarations and assignments of float, bool, vec2, vec3 and vec4; the ternary, logic,
 * comparison and arithmetic operators, componentwise on vectors with a scalar broadcast; swizzles; and the built-ins
 * below). Arithmetic is float64, so a result matches the GPU's float32 within a stated tolerance, never exactly.
 * Anything outside the subset throws, so a shader edit the tests cannot follow fails loudly instead of passing.
 */

export type GlslValue = number | boolean | number[];
export type GlslInputs = Record<string, GlslValue>;

type Token = { kind: 'num'; value: number } | { kind: 'id'; value: string } | { kind: 'op'; value: string };

const OPS = ['<=', '>=', '==', '!=', '&&', '||', '+', '-', '*', '/', '<', '>', '!', '?', ':', '(', ')', ',', '.', '='];

function tokenize(src: string): Token[] {
  const out: Token[] = [];
  let i = 0;
  while (i < src.length) {
    const c = src[i]!;
    if (/\s/.test(c)) {
      i += 1;
      continue;
    }
    const num = /^(\d+\.?\d*|\.\d+)(e[+-]?\d+)?/i.exec(src.slice(i));
    if (num && /[\d.]/.test(c) && !(c === '.' && !/\d/.test(src[i + 1] ?? ''))) {
      out.push({ kind: 'num', value: Number(num[0]) });
      i += num[0].length;
      continue;
    }
    const id = /^[A-Za-z_]\w*/.exec(src.slice(i));
    if (id) {
      out.push({ kind: 'id', value: id[0] });
      i += id[0].length;
      continue;
    }
    const op = OPS.find((o) => src.startsWith(o, i));
    if (!op) throw new Error(`glslSubset: unexpected character ${c}`);
    out.push({ kind: 'op', value: op });
    i += op.length;
  }
  return out;
}

const SWIZZLE: Record<string, number> = { x: 0, y: 1, z: 2, w: 3, r: 0, g: 1, b: 2, a: 3 };

function num(v: GlslValue): number {
  if (typeof v !== 'number') throw new Error('glslSubset: a scalar was expected');
  return v;
}

function zip(a: GlslValue, b: GlslValue, f: (x: number, y: number) => number): GlslValue {
  if (typeof a === 'boolean' || typeof b === 'boolean') throw new Error('glslSubset: arithmetic on a bool');
  if (typeof a === 'number' && typeof b === 'number') return f(a, b);
  const n = Array.isArray(a) ? a.length : (b as number[]).length;
  if (Array.isArray(a) && Array.isArray(b) && a.length !== b.length) throw new Error('glslSubset: vector sizes differ');
  return Array.from({ length: n }, (_, k) => f(Array.isArray(a) ? a[k]! : a, Array.isArray(b) ? b[k]! : b));
}

function map(a: GlslValue, f: (x: number) => number): GlslValue {
  if (typeof a === 'boolean') throw new Error('glslSubset: arithmetic on a bool');
  return typeof a === 'number' ? f(a) : a.map(f);
}

function flat(args: GlslValue[]): number[] {
  return args.flatMap((a) => (typeof a === 'boolean' ? [a ? 1 : 0] : typeof a === 'number' ? [a] : a));
}

function vec(n: number, args: GlslValue[]): number[] {
  const v = flat(args);
  if (v.length === 1) return Array.from({ length: n }, () => v[0]!);
  if (v.length !== n) throw new Error(`glslSubset: vec${n} given ${v.length} components`);
  return v;
}

const BUILTINS: Record<string, (args: GlslValue[]) => GlslValue> = {
  vec2: (a) => vec(2, a),
  vec3: (a) => vec(3, a),
  vec4: (a) => vec(4, a),
  float: (a) => num(a[0]!),
  length: ([a]) => Math.hypot(...flat([a!])),
  dot: ([a, b]) => flat([a!]).reduce((s, x, k) => s + x * flat([b!])[k]!, 0),
  sqrt: ([a]) => map(a!, Math.sqrt),
  log2: ([a]) => map(a!, Math.log2),
  exp: ([a]) => map(a!, Math.exp),
  abs: ([a]) => map(a!, Math.abs),
  pow: ([a, b]) => zip(a!, b!, Math.pow),
  min: ([a, b]) => zip(a!, b!, Math.min),
  max: ([a, b]) => zip(a!, b!, Math.max),
  clamp: ([a, lo, hi]) => zip(zip(a!, lo!, Math.max), hi!, Math.min),
  // a + (b - a) * t
  mix: ([a, b, t]) => zip(a!, zip(zip(b!, a!, (y, x) => y - x), t!, (d, s) => d * s), (x, d) => x + d),
};

const TYPES = new Set(['float', 'bool', 'vec2', 'vec3', 'vec4']);

class Parser {
  private at = 0;
  constructor(
    private readonly t: Token[],
    private readonly scope: Map<string, GlslValue>
  ) {}
  done(): boolean {
    return this.at >= this.t.length;
  }
  private peek(): Token | undefined {
    return this.t[this.at];
  }
  private isOp(v: string): boolean {
    const p = this.peek();
    return p?.kind === 'op' && p.value === v;
  }
  private take(v: string): void {
    if (!this.isOp(v)) throw new Error(`glslSubset: ${v} was expected`);
    this.at += 1;
  }
  expr(): GlslValue {
    const cond = this.binary(0);
    if (!this.isOp('?')) return cond;
    this.take('?');
    const yes = this.expr();
    this.take(':');
    const no = this.expr();
    if (typeof cond !== 'boolean') throw new Error('glslSubset: a ternary needs a bool');
    return cond ? yes : no;
  }
  private static readonly LEVELS: string[][] = [['||'], ['&&'], ['==', '!='], ['<', '>', '<=', '>='], ['+', '-'], ['*', '/']];
  private binary(level: number): GlslValue {
    if (level >= Parser.LEVELS.length) return this.unary();
    let left = this.binary(level + 1);
    for (;;) {
      const p = this.peek();
      if (p?.kind !== 'op' || !Parser.LEVELS[level]!.includes(p.value)) return left;
      this.at += 1;
      const right = this.binary(level + 1);
      left = apply(p.value, left, right);
    }
  }
  private unary(): GlslValue {
    if (this.isOp('!')) {
      this.take('!');
      const v = this.unary();
      if (typeof v !== 'boolean') throw new Error('glslSubset: ! needs a bool');
      return !v;
    }
    if (this.isOp('-')) {
      this.take('-');
      return map(this.unary(), (x) => -x);
    }
    return this.postfix();
  }
  private postfix(): GlslValue {
    let v = this.primary();
    while (this.isOp('.')) {
      this.take('.');
      const p = this.peek();
      if (p?.kind !== 'id') throw new Error('glslSubset: a swizzle was expected');
      this.at += 1;
      if (!Array.isArray(v)) throw new Error('glslSubset: a swizzle on a scalar');
      const parts = [...p.value].map((ch) => {
        const k = SWIZZLE[ch];
        if (k === undefined || k >= (v as number[]).length) throw new Error(`glslSubset: no component ${ch}`);
        return (v as number[])[k]!;
      });
      v = parts.length === 1 ? parts[0]! : parts;
    }
    return v;
  }
  private primary(): GlslValue {
    const p = this.peek();
    if (!p) throw new Error('glslSubset: the expression ended early');
    if (p.kind === 'num') {
      this.at += 1;
      return p.value;
    }
    if (p.kind === 'op' && p.value === '(') {
      this.take('(');
      const v = this.expr();
      this.take(')');
      return v;
    }
    if (p.kind === 'id') {
      this.at += 1;
      if (p.value === 'true' || p.value === 'false') return p.value === 'true';
      if (this.isOp('(')) {
        const f = BUILTINS[p.value];
        if (!f) throw new Error(`glslSubset: no built-in ${p.value}`);
        this.take('(');
        const args: GlslValue[] = [];
        if (!this.isOp(')')) {
          args.push(this.expr());
          while (this.isOp(',')) {
            this.take(',');
            args.push(this.expr());
          }
        }
        this.take(')');
        return f(args);
      }
      if (!this.scope.has(p.value)) throw new Error(`glslSubset: ${p.value} has no value`);
      return this.scope.get(p.value)!;
    }
    throw new Error(`glslSubset: unexpected ${p.value}`);
  }
}

function apply(op: string, a: GlslValue, b: GlslValue): GlslValue {
  switch (op) {
    case '||':
    case '&&':
      if (typeof a !== 'boolean' || typeof b !== 'boolean') throw new Error(`glslSubset: ${op} needs bools`);
      return op === '||' ? a || b : a && b;
    case '==':
      return num(a) === num(b);
    case '!=':
      return num(a) !== num(b);
    case '<':
      return num(a) < num(b);
    case '>':
      return num(a) > num(b);
    case '<=':
      return num(a) <= num(b);
    case '>=':
      return num(a) >= num(b);
    case '+':
      return zip(a, b, (x, y) => x + y);
    case '-':
      return zip(a, b, (x, y) => x - y);
    case '*':
      return zip(a, b, (x, y) => x * y);
    case '/':
      return zip(a, b, (x, y) => x / y);
    default:
      throw new Error(`glslSubset: no operator ${op}`);
  }
}

/**
 * Runs the shader's `main()` with the given attributes and uniforms (each by its GLSL name) and returns every variable
 * it wrote, its outputs (`gl_Position`, `gl_PointSize`, the `out` varyings) among them.
 */
export function runVertexMain(shader: string, inputs: GlslInputs): Record<string, GlslValue> {
  const open = shader.indexOf('void main()');
  if (open < 0) throw new Error('glslSubset: no main()');
  const body = shader.slice(shader.indexOf('{', open) + 1, shader.lastIndexOf('}'));
  const text = body.replace(/\/\/[^\n]*/g, '');
  if (/[{}]/.test(text)) throw new Error('glslSubset: blocks (if, for) are outside the subset');
  const scope = new Map<string, GlslValue>(Object.entries(inputs));
  const written: Record<string, GlslValue> = {};
  for (const statement of text.split(';').map((s) => s.trim()).filter(Boolean)) {
    const tokens = tokenize(statement);
    let k = 0;
    if (tokens[0]?.kind === 'id' && TYPES.has(tokens[0].value) && tokens[1]?.kind === 'id') k = 1;
    const name = tokens[k];
    const eq = tokens[k + 1];
    if (name?.kind !== 'id' || eq?.kind !== 'op' || eq.value !== '=') throw new Error(`glslSubset: not an assignment: ${statement}`);
    const parser = new Parser(tokens.slice(k + 2), scope);
    const value = parser.expr();
    if (!parser.done()) throw new Error(`glslSubset: text left over in ${statement}`);
    scope.set(name.value, value);
    written[name.value] = value;
  }
  return written;
}
