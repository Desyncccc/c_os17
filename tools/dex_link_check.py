#!/usr/bin/env python3
"""Check that an app's references into the platform still resolve on another platform build.

Usage: dex_link_check.py <app.apk> <library,library,...> --base /system=DIR /system_ext=DIR /product=DIR
                                                            --target /system=DIR /system_ext=DIR /product=DIR

The app is known to run on the "base" platform. Every class, method and field the app's dex files reference
outside the app itself is looked up on the class path the app gets on each platform (class hierarchy
included): the boot class path from /system/etc/classpaths/bootclasspath.pb, then the shared libraries named
in the second argument (the app's <uses-library> entries), resolved with their dependencies through the
platform's etc/permissions/*.xml. As in ART, the first definition of a class on that path wins.

A reference that resolves on the base but not on the target is what ART would report at run time as
NoClassDefFoundError / NoSuchMethodError / NoSuchFieldError, and is listed. References the base cannot
resolve from these jars either (classes in APEX jars, for example) are counted and ignored; where the
declaring class is known and the member is absent on both platforms a note is printed (the app has a latent
error there). Exit status 1 if anything is missing; with --strict also if there is a note.

       dex_link_check.py harvest <library,library,...> <out dir> /system=DIR /system_ext=DIR /product=DIR

copies what the check needs from a platform (boot class path list, boot jars, library jars, permission
files) into <out dir>/system, /system_ext and /product, so the platform images need not be kept.
"""
import re
import shutil
import struct
import sys
import zipfile
from pathlib import Path

NO_INDEX = 0xFFFFFFFF


def uleb(d, p):
    r = s = 0
    while True:
        b = d[p]
        p += 1
        r |= (b & 0x7F) << s
        if not b & 0x80:
            return r, p
        s += 7


class Dex:
    """One dex file (classic layout or one header of a v41 container)."""

    def __init__(self, data, base=0):
        d, self.d = data, data
        assert d[base:base + 4] == b"dex\n", "not a dex file"
        h = struct.unpack_from("<20I", d, base + 32)       # file_size .. data_off
        (self.file_size, _hs, _et, _ls, _lo, _mo, n_str, o_str, n_type, o_type, n_proto, o_proto,
         n_field, o_field, n_meth, o_meth, n_cls, o_cls, _ds, _do) = h
        self.strings_cache = {}
        self.o_str, self.n_str = o_str, n_str
        self.types = [struct.unpack_from("<I", d, o_type + 4 * i)[0] for i in range(n_type)]
        self.protos = [struct.unpack_from("<III", d, o_proto + 12 * i) for i in range(n_proto)]
        self.fields = [struct.unpack_from("<HHI", d, o_field + 8 * i) for i in range(n_field)]
        self.methods = [struct.unpack_from("<HHI", d, o_meth + 8 * i) for i in range(n_meth)]
        self.class_defs = [struct.unpack_from("<8I", d, o_cls + 32 * i) for i in range(n_cls)]

    def string(self, i):
        if i not in self.strings_cache:
            off, = struct.unpack_from("<I", self.d, self.o_str + 4 * i)
            _, p = uleb(self.d, off)
            end = self.d.index(b"\0", p)
            self.strings_cache[i] = self.d[p:end].decode("utf-8", "replace")
        return self.strings_cache[i]

    def type(self, i):
        return self.string(self.types[i])

    def proto(self, i):
        _, ret, params_off = self.protos[i]
        params = ""
        if params_off:
            n, = struct.unpack_from("<I", self.d, params_off)
            params = "".join(self.type(t) for t in struct.unpack_from(f"<{n}H", self.d, params_off + 4))
        return f"({params}){self.type(ret)}"

    def method_ref(self, i):
        c, p, n = self.methods[i]
        return self.type(c), self.string(n) + self.proto(p)

    def field_ref(self, i):
        c, t, n = self.fields[i]
        return self.type(c), self.string(n) + ":" + self.type(t)

    def classes(self):
        """Yield (name, superclass, interfaces, method set, field set) for every class defined here."""
        for cls, _flags, sup, ifaces_off, _src, _ann, data_off, _sv in self.class_defs:
            ifaces = []
            if ifaces_off:
                n, = struct.unpack_from("<I", self.d, ifaces_off)
                ifaces = [self.type(t) for t in struct.unpack_from(f"<{n}H", self.d, ifaces_off + 4)]
            methods, fields = set(), set()
            if data_off:
                p = data_off
                sizes = []
                for _ in range(4):
                    v, p = uleb(self.d, p)
                    sizes.append(v)
                for count in sizes[:2]:
                    idx = 0
                    for _ in range(count):
                        diff, p = uleb(self.d, p)
                        _, p = uleb(self.d, p)
                        idx += diff
                        fields.add(self.field_ref(idx)[1])
                for count in sizes[2:]:
                    idx = 0
                    for _ in range(count):
                        diff, p = uleb(self.d, p)
                        _, p = uleb(self.d, p)
                        _, p = uleb(self.d, p)
                        idx += diff
                        methods.add(self.method_ref(idx)[1])
            yield self.type(cls), (self.type(sup) if sup != NO_INDEX else None), ifaces, methods, fields


def dex_files(path):
    """Every dex in a jar/apk. A v41 container may hold several dex headers back to back."""
    out = []
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        return out
    for name in sorted(n for n in z.namelist() if n.startswith("classes") and n.endswith(".dex")):
        data = z.read(name)
        base = 0
        while base + 112 <= len(data) and data[base:base + 4] == b"dex\n":
            dex = Dex(data, base)
            out.append(dex)
            if data[base + 4:base + 7] < b"041" or dex.file_size == 0:
                break
            base += dex.file_size
    return out


class Platform:
    """Maps device paths to the unpacked partitions and works out an app's class path."""

    def __init__(self, mappings):
        self.roots = sorted((m.split("=", 1) for m in mappings), key=lambda kv: -len(kv[0]))

    def host(self, device_path):
        for prefix, directory in self.roots:
            if device_path == prefix or device_path.startswith(prefix + "/"):
                return Path(directory + device_path[len(prefix):])
        return None

    def boot_jars(self):
        pb = self.host("/system/etc/classpaths/bootclasspath.pb").read_bytes()
        return [j.decode() for j in re.findall(rb"/[\w./@+-]+\.jar", pb)]

    def libraries(self, names):
        """Jar paths of the named shared libraries and their dependencies, in resolution order."""
        known = {}
        for prefix, directory in self.roots:
            for xml in sorted(Path(directory).glob("etc/permissions/*.xml")):
                for tag in re.findall(r"<library\b[^>]*>", xml.read_text(errors="replace")):
                    attrs = dict(re.findall(r'([\w:-]+)\s*=\s*"([^"]*)"', tag))
                    if "name" in attrs and "file" in attrs:
                        known.setdefault(attrs["name"], (attrs["file"], attrs.get("dependency", "")))
        out, todo, seen = [], list(names), set()
        while todo:
            name = todo.pop(0)
            if name in seen:
                continue
            seen.add(name)
            if name not in known:
                out.append((name, None))
                continue
            jar, deps = known[name]
            out.append((name, jar))
            todo.extend(d for d in deps.split(":") if d)
        return out

    def index(self, library_names):
        index, used, missing = {}, [], []
        jars = [(None, j) for j in self.boot_jars()] + self.libraries(library_names)
        for lib, jar in jars:
            path = self.host(jar) if jar else None
            if path is None or not path.is_file():
                if lib:
                    missing.append(lib)
                continue                          # APEX jars are not unpacked here
            used.append(jar)
            for dex in dex_files(path):
                for name, sup, ifaces, methods, fields in dex.classes():
                    index.setdefault(name, (sup, ifaces, methods, fields))
        return index, used, missing


def resolve(index, cls, member, kind):
    """'yes', 'no' (whole hierarchy known, member absent) or 'unknown' (hierarchy leaves the index)."""
    if cls.startswith("["):
        return "unknown"
    if member and member.startswith("<init>"):       # constructors are not inherited
        if cls not in index:
            return "unknown"
        return "yes" if member in index[cls][2] else "no"
    seen, todo, complete = set(), [cls], True
    while todo:
        c = todo.pop()
        if c in seen:
            continue
        seen.add(c)
        if c not in index:
            complete = False
            continue
        sup, ifaces, methods, fields = index[c]
        if member is None or member in (methods if kind == "method" else fields):
            return "yes"
        todo.extend(ifaces)
        if sup:
            todo.append(sup)
    return "no" if complete else "unknown"


def harvest(libs, out, mappings):
    platform = Platform(mappings)
    wanted = ["/system/etc/classpaths/bootclasspath.pb"] + platform.boot_jars()
    wanted += [jar for _, jar in platform.libraries(libs) if jar]
    for prefix, directory in platform.roots:
        for xml in sorted(Path(directory).glob("etc/permissions/*.xml")):
            if "<library" in xml.read_text(errors="replace"):
                wanted.append(f"{prefix}/etc/permissions/{xml.name}")
    copied = 0
    for device_path in dict.fromkeys(wanted):
        src = platform.host(device_path)
        if src is None or not src.is_file():
            continue                                  # APEX jars
        dst = Path(out + device_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        copied += 1
    print(f"harvested {copied} class-path files into {out}")


def main():
    args = sys.argv[1:]
    if args and args[0] == "harvest":
        return harvest([x for x in args[1].split(",") if x], args[2], args[3:])
    strict = "--strict" in args
    args = [a for a in args if a != "--strict"]
    app, libs, base_paths, target_paths, cur = args[0], [x for x in args[1].split(",") if x], [], [], None
    for a in args[2:]:
        if a in ("--base", "--target"):
            cur = base_paths if a == "--base" else target_paths
        else:
            cur.append(a)
    app_dex = dex_files(app)
    own = {name for d in app_dex for name, *_ in d.classes()}
    refs = set()
    for d in app_dex:
        for i in range(len(d.methods)):
            c, m = d.method_ref(i)
            if c not in own:
                refs.add((c, m, "method"))
        for i in range(len(d.fields)):
            c, f = d.field_ref(i)
            if c not in own:
                refs.add((c, f, "field"))
        for t in range(len(d.types)):
            c = d.type(t)
            if c.startswith("L") and c not in own:
                refs.add((c, None, "class"))
    base, base_jars, _ = Platform(base_paths).index(libs)
    target, target_jars, target_missing = Platform(target_paths).index(libs)
    print(f"app: {len(own)} classes, {len(refs)} external references")
    print(f"base class path: {len(base_jars)} jars, {len(base)} classes; "
          f"target class path: {len(target_jars)} jars, {len(target)} classes")
    if target_missing:
        print(f"  MISSING shared libraries on the target: {target_missing}")
    lost, ignored, broken_on_base = [], 0, []
    for cls, member, kind in sorted(refs, key=lambda r: (r[0], r[1] or "", r[2])):
        if kind == "class":
            b = "yes" if cls in base else "unknown"
            t = "yes" if cls in target else "unknown"
        else:
            b, t = resolve(base, cls, member, kind), resolve(target, cls, member, kind)
        if b == "yes" and t != "yes":
            lost.append((kind, cls, member))
        elif b != "yes":
            ignored += 1
            if b == "no" and t != "yes":        # declaring class known on the base, member absent on both
                broken_on_base.append((kind, cls, member))
    print(f"{ignored} references are not resolvable from the base jars given (APEX or runtime classes): ignored")
    for kind, cls, member in broken_on_base:
        print(f"  note: {kind} {cls}->{member} is absent on the base platform as well")
    for kind, cls, member in lost:
        print(f"  MISSING {kind}: {cls}" + (f"->{member}" if member else ""))
    if lost or target_missing or (strict and broken_on_base):
        sys.exit(1)
    print("every reference that resolves on the base platform also resolves on the target")


if __name__ == "__main__":
    main()
