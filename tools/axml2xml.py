#!/usr/bin/env python3
"""Turn `aapt2 dump xmltree` output (read from stdin) back into a plain-text XML document (stdout).

Used to recover the device's power_profile.xml from the base ROM's compiled framework overlay: ColorOS reads
<partition>/etc/power_profile/power_profile.xml as text, the base ROM only ships the compiled resource.
Handles elements (E:), attributes (A:) and text nodes (T:); namespaces are dropped (power profiles have none).
"""
import re
import sys
from xml.sax.saxutils import escape, quoteattr


def main():
    out = ['<?xml version="1.0" encoding="utf-8"?>']
    stack = []          # (indent, tag)
    pending = None      # element whose start tag is still open: [indent, tag, attrs, text]

    def flush(next_indent):
        nonlocal pending
        if pending is not None:
            indent, tag, attrs, text = pending
            pad = "    " * len(stack)
            start = f"{pad}<{tag}" + "".join(f" {k}={quoteattr(v)}" for k, v in attrs)
            if text is not None:
                out.append(f"{start}>{escape(text)}</{tag}>")
            elif next_indent is not None and next_indent > indent:
                out.append(start + ">")
                stack.append((indent, tag))
            else:
                out.append(start + " />")
            pending = None
        while stack and (next_indent is None or stack[-1][0] >= next_indent):
            _, tag = stack.pop()
            out.append("    " * len(stack) + f"</{tag}>")

    for line in sys.stdin:
        m = re.match(r"^(\s*)E: (\S+)", line)
        if m:
            flush(len(m.group(1)))
            pending = [len(m.group(1)), m.group(2).split(":")[-1], [], None]
            continue
        m = re.match(r'^\s*A: (\S+?)(?:\(0x[0-9a-f]+\))?="(.*)" \(Raw: ".*"\)\s*$', line) or \
            re.match(r'^\s*A: (\S+?)(?:\(0x[0-9a-f]+\))?=(.*)$', line)
        if m and pending is not None:
            pending[2].append((m.group(1).split(":")[-1].split("/")[-1], m.group(2).strip()))
            continue
        m = re.match(r"^\s*T: '(.*)'\s*$", line)
        if m and pending is not None:
            pending[3] = m.group(1)
    flush(None)
    print("\n".join(out))


if __name__ == "__main__":
    main()
