"""Standalone GPL-3.0-or-later decompression helper using pyooz.

Copyright (c) 2026 MHW Layer Studio contributors.
This helper is distributed under GPL-3.0-or-later; see COPYING.txt.
It reads bounded binary frames and never opens user files.
"""
import struct
import sys

import ooz


def read_exact(stream, count):
    data = bytearray()
    while len(data) < count:
        block = stream.read(count - len(data))
        if not block:
            raise EOFError()
        data.extend(block)
    return bytes(data)


def main():
    source, target = sys.stdin.buffer, sys.stdout.buffer
    while True:
        try:
            header = read_exact(source, 8)
        except EOFError:
            return
        length, expected = struct.unpack("<II", header)
        if not 1 <= length <= 1024 * 1024 or expected != 0x40000:
            return
        data = read_exact(source, length)
        try:
            result = ooz.decompress(data, expected)
            target.write(struct.pack("<I", len(result)) + result)
        except Exception:
            target.write(struct.pack("<I", 0))
        target.flush()


if __name__ == "__main__":
    main()
