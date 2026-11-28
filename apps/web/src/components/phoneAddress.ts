/**
 * phone-code-home-one (UIUX-phone-code-READY.md Q3, DECIDED-BY-LEAD 19:25 on 27 Nov): the phone's address the laptop's
 * home offers as a code, and how the code is drawn.
 *
 * The address is the one the field start prints after "Phone on the same network: ", handed to the build made at that
 * start as VITE_ALBEDIAN_PHONE_URL (scripts/field.mjs, --lan only). The home offers it only at the laptop's own host,
 * never on the network address, so never on a phone; and only a plain http address of printable ASCII, which the
 * encoder takes whole (scripts/qr.mjs) and which a phone's camera opens as itself.
 */

/** The laptop's own host names, as `location.hostname` gives them. */
export const LAPTOP_HOSTS: readonly string[] = ['localhost', '127.0.0.1', '[::1]'];

/** The address to offer, or null: none served, not at the laptop's own host, or not a plain http address. */
export function connectPhoneAddress(served: string | undefined, hostname: string): string | null {
  if (!served || !LAPTOP_HOSTS.includes(hostname)) return null;
  return /^http:\/\/[\x21-\x7e]+$/.test(served) ? served : null;
}

/** The address served to this build, read once: VITE_ALBEDIAN_PHONE_URL, absent outside a --lan build. */
export function servedPhoneAddress(): string | null {
  const env = (import.meta as ImportMeta & { readonly env?: { readonly VITE_ALBEDIAN_PHONE_URL?: string } }).env;
  return typeof window === 'undefined' ? null : connectPhoneAddress(env?.VITE_ALBEDIAN_PHONE_URL, window.location.hostname);
}

/**
 * The code's square in whole CSS pixels (Q3): each module floor(240 / (modules + 8)) px, the standard's four module quiet
 * zone inside the square on every side, so the square is a module times (modules + 8) px (version 2: 7 px, 231 px).
 */
export function phoneCodeGeometry(modules: number): { modulePx: number; sidePx: number; quietPx: number } {
  const modulePx = Math.floor(240 / (modules + 8));
  return { modulePx, sidePx: modulePx * (modules + 8), quietPx: modulePx * 4 };
}
