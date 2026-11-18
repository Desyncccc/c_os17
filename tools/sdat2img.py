#!/usr/bin/env python3
"""Convert an Android block-based OTA payload (<name>.transfer.list + <name>.new.dat[.br]) to a raw image.

Usage: sdat2img.py <transfer.list> <new.dat | new.dat.br | -> <out.img>

Only full-image OTAs are supported (commands: new, erase, zero). A ".br" input is decompressed with the
`brotli` command-line tool; "-" reads the already-decompressed stream from stdin.
"""
import subprocess
import sys

BLOCK = 4096


def ranges(text):
    nums = [int(x) for x in text.split(",")]
    assert nums[0] == len(nums) - 1 and nums[0] % 2 == 0, "bad range set: " + text[:60]
    return [(nums[i], nums[i + 1]) for i in range(1, len(nums), 2)]


def main():
    tlist, src, out = sys.argv[1:4]
    lines = open(tlist).read().split("\n")
    version = int(lines[0])
    total_blocks = int(lines[1])
    body = lines[4:] if version >= 2 else lines[2:]

    proc = None
    if src == "-":
        stream = sys.stdin.buffer
    elif src.endswith(".br"):
        proc = subprocess.Popen(["brotli", "-dc", src], stdout=subprocess.PIPE, bufsize=1 << 20)
        stream = proc.stdout
    else:
        stream = open(src, "rb")

    max_block = 0
    with open(out, "wb") as o:
        for line in body:
            if not line.strip():
                continue
            cmd, _, arg = line.partition(" ")
            if cmd == "new":
                for a, b in ranges(arg):
                    o.seek(a * BLOCK)
                    left = (b - a) * BLOCK
                    while left:
                        chunk = stream.read(min(left, 1 << 22))
                        if not chunk:
                            raise SystemExit("new.dat ended early")
                        o.write(chunk)
                        left -= len(chunk)
                    max_block = max(max_block, b)
            elif cmd in ("erase", "zero"):
                for a, b in ranges(arg):
                    max_block = max(max_block, b)
            else:
                raise SystemExit("unsupported transfer command (incremental OTA?): " + cmd)
        o.truncate(max(max_block, total_blocks) * BLOCK)
    if proc and proc.wait() != 0:
        raise SystemExit("brotli failed")
    print(f"{out}: {max(max_block, total_blocks) * BLOCK} bytes")


if __name__ == "__main__":
    main()
