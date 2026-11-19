/**
 * A photo dragged onto the home's window (arrive, approved 2026-11-12, screen 2). What a drag event carries, read
 * the way a browser gives it: while a file is over the window the browser names the kind (`types` holds "Files")
 * and withholds the list; the list arrives with the drop.
 */

/** The part of a drag event's DataTransfer the home reads. */
export interface DragCarried {
  readonly types: ArrayLike<string>;
  readonly files: ArrayLike<File>;
}

/** Whether the drag holds files: a drag of text or of a link does not, and the home leaves it alone. */
export function carriesFiles(data: DragCarried | null | undefined): boolean {
  if (!data) return false;
  return Array.from(data.types).includes('Files');
}

/** The file a drop hands over: its first, as the picker takes one; none when the drop holds no file. */
export function droppedFile(data: DragCarried | null | undefined): File | null {
  if (!carriesFiles(data)) return null;
  return data!.files[0] ?? null;
}
