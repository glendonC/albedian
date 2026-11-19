import { useEffect, useState } from 'react';
import { LANDSCAPE_PHONE_QUERY, PHONE_QUERY } from '../components/phoneSheet.js';

/**
 * Whether the window is a phone's (upright or sideways), kept current as it rotates or resizes. The approved laptop
 * screens are built above that width; the phone's own approved screens are their own units.
 */
export function usePhoneLayout(): boolean {
  const read = (): boolean => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
    return window.matchMedia(PHONE_QUERY).matches || window.matchMedia(LANDSCAPE_PHONE_QUERY).matches;
  };
  const [phone, setPhone] = useState<boolean>(read);
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined;
    const lists = [window.matchMedia(PHONE_QUERY), window.matchMedia(LANDSCAPE_PHONE_QUERY)];
    const onChange = () => setPhone(read());
    onChange();
    lists.forEach((list) => list.addEventListener('change', onChange));
    return () => lists.forEach((list) => list.removeEventListener('change', onChange));
  }, []);
  return phone;
}
