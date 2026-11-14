"""Direct big-endian FITS header, memory-mapped image, and float-column reader."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

FITS_BLOCK = 2880
_FITS_DTYPES = {8: ">u1", 16: ">i2", 32: ">i4", 64: ">i8", -32: ">f4", -64: ">f8"}


@dataclass(frozen=True)
class FitsHdu:
    header: dict[str, str]
    data_offset: int
    # numpy order, slowest axis first: (NAXISn, ..., NAXIS1)
    shape: tuple[int, ...]
    bitpix: int


def _card_value(card: str) -> str:
    raw = card[10:].lstrip()
    if raw.startswith("'"):
        text, i = [], 1
        while i < len(raw):
            if raw[i] == "'":
                if raw[i + 1 : i + 2] == "'":
                    text.append("'")
                    i += 2
                    continue
                break
            text.append(raw[i])
            i += 1
        return "".join(text).rstrip()
    return raw.split("/", 1)[0].strip()


def read_fits_hdus(path: Path) -> list[FitsHdu]:
    """Every HDU of a FITS file: its header keywords, where its data starts, its shape."""
    hdus: list[FitsHdu] = []
    size = Path(path).stat().st_size
    with open(path, "rb") as fh:
        offset = 0
        while offset < size:
            fh.seek(offset)
            cards: list[str] = []
            while True:
                block = fh.read(FITS_BLOCK)
                if len(block) < FITS_BLOCK:
                    raise ValueError(f"{path}: header runs past the end of the file")
                ended = False
                for i in range(0, FITS_BLOCK, 80):
                    card = block[i : i + 80].decode("ascii")
                    if card[:8].rstrip() == "END":
                        ended = True
                        break
                    cards.append(card)
                if ended:
                    break
            header_bytes = math.ceil((len(cards) + 1) * 80 / FITS_BLOCK) * FITS_BLOCK
            header = {card[:8].rstrip(): _card_value(card) for card in cards if card[8:10] == "= "}
            bitpix = int(header["BITPIX"])
            naxis = int(header["NAXIS"])
            axes = [int(header[f"NAXIS{k}"]) for k in range(1, naxis + 1)]
            count = int(np.prod(axes)) if naxis else 0
            data_bytes = abs(bitpix) // 8 * int(header.get("GCOUNT", "1")) * (int(header.get("PCOUNT", "0")) + count)
            hdus.append(FitsHdu(header, offset + header_bytes, tuple(reversed(axes)), bitpix))
            offset += header_bytes + math.ceil(data_bytes / FITS_BLOCK) * FITS_BLOCK
    return hdus


def named_hdu(hdus: list[FitsHdu], name: str) -> FitsHdu:
    """Return the first HDU with matching EXTNAME, or raise ValueError."""
    for hdu in hdus:
        if hdu.header.get("EXTNAME") == name:
            return hdu
    raise ValueError(f"no HDU named {name!r}")


_named = named_hdu


def fits_image(path: Path, hdu: FitsHdu) -> np.ndarray:
    return np.memmap(path, dtype=_FITS_DTYPES[hdu.bitpix], mode="r", offset=hdu.data_offset, shape=hdu.shape)


def fits_float_column(path: Path, hdu: FitsHdu) -> np.ndarray:
    """The single 32-bit float column of a one-column binary table."""
    h = hdu.header
    if h.get("XTENSION") != "BINTABLE" or h.get("TFIELDS") != "1" or h.get("TFORM1") != "E" or h.get("NAXIS1") != "4":
        raise ValueError(f"expected a one-column float table, got {h}")
    return np.fromfile(path, dtype=">f4", count=int(h["NAXIS2"]), offset=hdu.data_offset).astype(np.float64)


__all__ = [
    "FITS_BLOCK",
    "_FITS_DTYPES",
    "FitsHdu",
    "_card_value",
    "read_fits_hdus",
    "named_hdu",
    "_named",
    "fits_image",
    "fits_float_column",
]
