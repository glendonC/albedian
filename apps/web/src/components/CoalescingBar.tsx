import React, { useCallback, useEffect, useRef } from 'react';

export type CoalescingPatternKind = 'slats' | 'dither' | 'halftone' | 'hatch';

export interface CoalescingBarPalette {
  solidStart: string;
  solidMid: string;
  solidEnd: string;
  wavefrontMid: string;
  wavefrontDim: string;
  restPattern: string;
}

/**
 * Locked-in Albedian Flat Architectural Vapor-Silver + Luminous Wavefront Refraction palette:
 * - Zero vertical cylinder/pipe shadow (100% flat vertically like @zzzzshawn & Lusion).
 * - High-albedo Vapor-Silver solid fill (#D4DCE7 -> #EDF2F8 -> #FFFFFF at fillX).
 * - Coalescing wavefront dots/slats refract through Dichroic Ice-Cobalt (#5A87E8 -> #355291)
 *   into recessed gunmetal-slate (#252D3D) on the #07080A void.
 */
export const ALBEDIAN_FLAT_WAVEFRONT_PALETTE: CoalescingBarPalette = {
  solidStart: '#D4DCE7',
  solidMid: '#EDF2F8',
  solidEnd: '#FFFFFF',
  wavefrontMid: '#5A87E8',
  wavefrontDim: '#355291',
  restPattern: '#252D3D',
};

export const ALBEDIAN_PURE_SILVER_PALETTE: CoalescingBarPalette = {
  solidStart: '#D4DCE7',
  solidMid: '#EDF1F7',
  solidEnd: '#FFFFFF',
  wavefrontMid: '#9CA6B5',
  wavefrontDim: '#525B69',
  restPattern: '#282E38',
};

export interface CoalescingBarProps {
  pattern: CoalescingPatternKind;
  value: number; // 0..1
  width: number;
  height: number;
  palette?: CoalescingBarPalette;
  onChange?: (nextValue: number) => void;
  ariaLabel?: string;
  className?: string;
  testId?: string;
}

const BAYER_4X4 = [
  [0, 8, 2, 10],
  [12, 4, 14, 6],
  [3, 11, 1, 9],
  [15, 7, 13, 5],
] as const;

export function renderCoalescingBarCanvas(
  canvas: HTMLCanvasElement,
  width: number,
  height: number,
  progress: number,
  pattern: CoalescingPatternKind,
  pal: CoalescingBarPalette = ALBEDIAN_FLAT_WAVEFRONT_PALETTE
): void {
  const clamped = Math.max(0, Math.min(1, progress));
  const dpr = typeof window !== 'undefined' ? Math.min(2, window.devicePixelRatio || 1) : 2;
  if (canvas.width !== width * dpr || canvas.height !== height * dpr) {
    canvas.width = width * dpr;
    canvas.height = height * dpr;
  }
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  ctx.save();
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  // 1. Draw unfilled micro-pattern directly on void (no heavy track box)
  ctx.save();
  ctx.beginPath();
  ctx.rect(0, 0, width, height);
  ctx.clip();

  if (pattern === 'slats') {
    const pitch = height <= 16 ? 5 : Math.max(7, Math.round(height * 0.3));
    const barW = height <= 16 ? 1.8 : Math.max(1.8, Math.min(2.2, height * 0.08));
    ctx.fillStyle = '#43567A';
    const totalCols = Math.floor(width / pitch);
    for (let c = 0; c <= totalCols; c++) {
      const x = Math.min(width - barW, c * pitch + (pitch - barW) * 0.5);
      ctx.fillRect(x, 0, barW, height);
    }
  } else if (pattern === 'dither') {
    const cell = Math.max(3, Math.round(height / 8));
    const rows = Math.floor(height / cell);
    const cols = Math.floor(width / cell);
    ctx.fillStyle = '#3E5073';
    for (let c = 0; c < cols; c++) {
      for (let r = 0; r < rows; r++) {
        if ((r + c) % 2 === 0 && (r % 2 === 0 || (c + r) % 4 === 0)) {
          ctx.fillRect(c * cell, r * cell, cell * 0.88, cell * 0.88);
        }
      }
    }
  } else if (pattern === 'halftone') {
    const rp = Math.max(4.2, height / 7);
    const cp = rp * 1.26;
    const dotR = Math.max(1.2, rp * 0.28);
    ctx.fillStyle = '#3E5073';
    for (let r = 0; r <= Math.ceil(height / rp); r++) {
      const y = r * rp + rp * 0.42;
      const xOff = r % 2 === 1 ? cp * 0.5 : 0;
      for (let c = 0; c <= Math.ceil(width / cp); c++) {
        ctx.beginPath();
        ctx.arc(c * cp + xOff, y, dotR, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  } else {
    const spacing = Math.max(8, Math.round(height * 0.3));
    ctx.strokeStyle = '#3E5073';
    ctx.lineWidth = Math.max(1.6, height * 0.065);
    for (let x = -height; x < width + height; x += spacing) {
      ctx.beginPath();
      ctx.moveTo(x, height);
      ctx.lineTo(x + height, 0);
      ctx.stroke();
    }
  }
  ctx.restore();

  // 2. Build coalescing active mask on an offscreen canvas
  const off = document.createElement('canvas');
  off.width = width * dpr;
  off.height = height * dpr;
  const mctx = off.getContext('2d');
  if (!mctx) {
    ctx.restore();
    return;
  }
  mctx.scale(dpr, dpr);
  mctx.save();
  mctx.beginPath();
  mctx.rect(0, 0, width, height);
  mctx.clip();
  mctx.fillStyle = '#ffffff';

  let transitionW = 56;
  const fillX = width * clamped;

  if (pattern === 'slats') {
    const pitch = height <= 16 ? 5 : Math.max(7, Math.round(height * 0.3));
    const minW = height <= 16 ? 1.8 : Math.max(1.8, Math.min(2.2, height * 0.08));
    const fillCol = Math.floor(fillX / pitch);
    const solidW = fillCol * pitch;
    const numTransCols = height <= 16 ? 3 : 4;
    transitionW = pitch * numTransCols;
    mctx.fillRect(0, 0, Math.max(0, solidW), height);
    for (let c = fillCol; c < fillCol + numTransCols && c <= Math.floor(width / pitch); c++) {
      const k = c - fillCol;
      const t = 1 - (k + 0.25) / numTransCols;
      const bw = minW + (pitch - 1.4 - minW) * Math.pow(t, 1.3);
      const offset = k === 0 ? 1.2 : (pitch - bw) * 0.45;
      const bx = c * pitch + offset;
      mctx.fillRect(bx, 0, Math.min(bw, width - bx), height);
    }
  } else if (pattern === 'dither') {
    const cell = Math.max(3, Math.round(height / 9));
    const rows = Math.floor(height / cell);
    const cols = Math.floor(width / cell);
    const fillCol = Math.floor(cols * clamped);
    transitionW = cell * 9;
    mctx.fillRect(0, 0, Math.max(0, (fillCol - 2) * cell), height);
    for (let c = Math.max(0, fillCol - 3); c <= Math.min(cols - 1, fillCol + 5); c++) {
      const t = 1 - (c - fillCol + 3) / 9;
      for (let r = 0; r < rows; r++) {
        const thresh = (BAYER_4X4[r % 4]?.[c % 4] ?? 0) / 16;
        if (t > thresh * 0.85) {
          mctx.fillRect(c * cell, r * cell, cell, cell);
        }
      }
    }
  } else if (pattern === 'halftone') {
    const rp = Math.max(4.2, height / 7);
    const cp = rp * 1.26;
    const minR = Math.max(1.1, rp * 0.27);
    transitionW = Math.max(44, height * 1.75);
    mctx.fillRect(0, 0, Math.max(0, fillX - 10), height);
    for (let r = 0; r <= Math.ceil(height / rp); r++) {
      const y = r * rp + rp * 0.42;
      const xOff = r % 2 === 1 ? cp * 0.5 : 0;
      for (let c = 0; c <= Math.ceil(width / cp); c++) {
        const x = c * cp + xOff;
        if (x < fillX - 16 || x > fillX + transitionW) continue;
        const dist = Math.max(0, x - (fillX - 10));
        const t = Math.max(0, 1 - dist / transitionW);
        const radius = minR + rp * 0.92 * Math.pow(t, 1.6);
        mctx.beginPath();
        mctx.arc(x, y, radius, 0, Math.PI * 2);
        mctx.fill();
      }
    }
  } else {
    const cell = Math.max(2.5, height / 10);
    transitionW = Math.max(36, height * 1.25);
    mctx.fillRect(0, 0, Math.max(0, fillX - 14), height);
    for (let r = 0; r < Math.ceil(height / cell); r++) {
      const y = r * cell;
      const rf = fillX - 14 + (height - y) * 0.65;
      mctx.fillRect(0, y, Math.max(0, rf), cell + 0.5);
      for (let k = 0; k < 5; k++) {
        if ((r + k * 3) % 3 !== 0) {
          const sz = Math.max(1.4, cell * (1 - k * 0.18));
          mctx.fillRect(rf + k * cell * 1.35, y, sz, sz);
        }
      }
    }
  }
  mctx.restore();

  // 3. Apply balanced Vapor-Silver -> Ice-Azure -> Dichroic Wavefront progression (source-in)
  mctx.globalCompositeOperation = 'source-in';
  const maxGradX = Math.min(width, fillX + transitionW);
  const stopWaveStart = Math.max(0.12, Math.min(0.92, fillX / Math.max(1, maxGradX)));

  const hg = mctx.createLinearGradient(0, 0, Math.max(24, maxGradX), 0);
  hg.addColorStop(0, pal.solidStart);
  hg.addColorStop(stopWaveStart * 0.36, pal.solidMid);
  hg.addColorStop(stopWaveStart * 0.68, '#B8D0FA');
  hg.addColorStop(stopWaveStart * 0.92, '#7EA5F5');
  hg.addColorStop(Math.min(0.98, stopWaveStart + (1 - stopWaveStart) * 0.45), pal.wavefrontMid);
  hg.addColorStop(1, '#4872D6');
  mctx.fillStyle = hg;
  mctx.fillRect(0, 0, width, height);

  ctx.drawImage(off, 0, 0, width, height);
  ctx.restore();
}

export const CoalescingBar: React.FC<CoalescingBarProps> = ({
  pattern,
  value,
  width,
  height,
  palette = ALBEDIAN_FLAT_WAVEFRONT_PALETTE,
  onChange,
  ariaLabel,
  className,
  testId,
}) => {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const draggingRef = useRef(false);

  useEffect(() => {
    if (!canvasRef.current) return;
    renderCoalescingBarCanvas(canvasRef.current, width, height, value, pattern, palette);
  }, [pattern, value, width, height, palette]);

  const updateFromClientX = useCallback(
    (clientX: number) => {
      if (!onChange || !canvasRef.current) return;
      const rect = canvasRef.current.getBoundingClientRect();
      const next = Math.max(0.04, Math.min(1, (clientX - rect.left) / Math.max(1, rect.width)));
      onChange(next);
    },
    [onChange]
  );

  return (
    <canvas
      ref={canvasRef}
      data-testid={testId}
      data-hud-interactive="true"
      role={onChange ? 'slider' : 'progressbar'}
      aria-label={ariaLabel}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(value * 100)}
      className={className}
      style={{
        width: `${width}px`,
        height: `${height}px`,
        display: 'block',
        cursor: onChange ? 'ew-resize' : 'default',
      }}
      onPointerDown={(e) => {
        if (!onChange) return;
        e.stopPropagation();
        draggingRef.current = true;
        e.currentTarget.setPointerCapture(e.pointerId);
        updateFromClientX(e.clientX);
      }}
      onPointerMove={(e) => {
        if (!draggingRef.current) return;
        e.stopPropagation();
        updateFromClientX(e.clientX);
      }}
      onPointerUp={(e) => {
        if (!draggingRef.current) return;
        e.stopPropagation();
        draggingRef.current = false;
      }}
    />
  );
};
