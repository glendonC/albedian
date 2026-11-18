import fs from 'node:fs';

// DevTools replies of 3 MB arrive; replies of 4.7 MB and more never do (measured with Chrome 156
// and Node's WebSocket), and a busy 1080p frame is a 4.7 MB data URL. Frames stay in the page,
// are compared by SHA-256 there, and come over in pieces of this many characters.
export const CHUNK_CHARS = 1_000_000;
export const DEFAULT_EVALUATE_TIMEOUT_MS = 120_000;

export class DevTools {
  constructor(ws) {
    this.ws = ws;
    this.nextId = 0;
    this.pending = new Map();
    this.listeners = new Set();

    ws.addEventListener('message', (ev) => {
      const text = typeof ev.data === 'string' ? ev.data : Buffer.from(ev.data).toString('utf8');
      const msg = JSON.parse(text);
      if (msg.id !== undefined && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(`${msg.error.message} ${msg.error.data ?? ''}`.trim()));
        else resolve(msg.result);
      } else if (msg.method) {
        for (const listener of this.listeners) listener(msg);
      }
    });

    if (typeof ws.addEventListener === 'function') {
      ws.addEventListener('close', () => {
        for (const { reject } of this.pending.values()) {
          reject(new Error('DevTools WebSocket closed before reply'));
        }
        this.pending.clear();
      });
    }
  }

  send(method, params = {}, sessionId) {
    const id = ++this.nextId;
    const frame = sessionId ? { id, method, params, sessionId } : { id, method, params };
    this.ws.send(JSON.stringify(frame));
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
  }

  on(listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }
}

export async function evaluateInPage(
  devtools,
  sessionId,
  expression,
  timeoutMs = DEFAULT_EVALUATE_TIMEOUT_MS,
) {
  const res = await devtools.send(
    'Runtime.evaluate',
    { expression, awaitPromise: true, returnByValue: true, timeout: timeoutMs },
    sessionId,
  );
  if (res.exceptionDetails) {
    throw new Error(`page: ${JSON.stringify(res.exceptionDetails).slice(0, 1200)}`);
  }
  return res.result.value;
}

/** Transfers the last stored frame data URL from the page in fixed-size chunks. */
export async function fetchStoredDataUrl(devtools, sessionId, length, chunkChars = CHUNK_CHARS) {
  let dataUrl = '';
  for (let start = 0; start < length; start += chunkChars) {
    dataUrl += await evaluateInPage(
      devtools,
      sessionId,
      `window.__capture.piece(${start}, ${start + chunkChars})`,
    );
  }
  if (dataUrl.length !== length) {
    throw new Error(`frame arrived with ${dataUrl.length} of ${length} characters`);
  }
  return dataUrl;
}

/** Decodes a base64 `data:...;base64,...` string into a raw byte Buffer. */
export function decodeDataUrlPayload(dataUrl) {
  const commaIdx = String(dataUrl).indexOf(',');
  if (commaIdx < 0) {
    throw new Error('Malformed data URL: missing comma delimiter');
  }
  return Buffer.from(String(dataUrl).slice(commaIdx + 1), 'base64');
}

export function writeDataUrl(file, dataUrl, fsImpl = fs) {
  fsImpl.writeFileSync(file, decodeDataUrlPayload(dataUrl));
}
