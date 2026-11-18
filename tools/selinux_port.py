#!/usr/bin/env python3
"""SELinux adaptation of a ColorOS system image to another vendor's policy. All edits are made in place
(same inode), so file ownership, modes and security.* xattrs of the extracted trees are kept.

Sub-commands (system root = root of the system image, containing system/, system_ext/, product/):

  conflicts   <system root> <vendor root> <odm root>   list type/attribute names declared on both sides
  propctx     <system root> <vendor root> <odm root>   list property_contexts entries defined differently
  contexts    <system root> <vendor root> <odm root>   list service/hwservice/keystore2 context clashes
  fix-system  <system root> <vendor root> <odm root>   strip logging-only xperm rules, drop clashing
                                                       property_contexts entries from the system side
  fix-vendor  <system root> <vendor root> <odm root>   resolve declaration clashes in the vendor policy,
                                                       strip xperm rules from plat_pub_versioned.cil

Why (all are fatal at boot, before any log can be read off the phone):
  * second-stage init compiles plat + system_ext + product + vendor + odm CIL into one policy; a name declared
    twice with different kinds makes secilc fail -> init fatal;
  * the ColorOS GSI init carries the TrebleDroid legacy-kernel path: on a 4.14 kernel it caps the policy to
    version 29 and strips only `allowx` rules. `auditallowx`/`dontauditx` (logging only) and the xperm rules in
    the vendor's plat_pub_versioned.cil stay and fail a version-29 compile;
  * init merges every *_property_contexts file into one trie; the same name with two different contexts
    aborts property-area setup.
Findings credit: the OnePlus 7 Pro ColorOS 17 port (github.com/nathanatgit/oneplus7-coloros17-port).
"""
import re
import sys
from pathlib import Path

DECL = re.compile(r"^\((type|typeattribute)\s+([^\s()]+)\)", re.M)
XPERM_LOG = re.compile(r"^\((?:auditallowx|dontauditx) .*\n", re.M)
XPERM_ALL = re.compile(r"^\((?:allowx|auditallowx|dontauditx|neverallowx) .*\n", re.M)


def system_cils(s):
    return [s / "system/etc/selinux/plat_sepolicy.cil", s / "system_ext/etc/selinux/system_ext_sepolicy.cil",
            s / "product/etc/selinux/product_sepolicy.cil"]


def vendor_cils(v, o):
    return [v / "etc/selinux/vendor_sepolicy.cil", o / "etc/selinux/odm_sepolicy.cil"]


def system_propctx(s):
    return [s / "system/etc/selinux/plat_property_contexts",
            s / "system_ext/etc/selinux/system_ext_property_contexts",
            s / "product/etc/selinux/product_property_contexts"]


def vendor_propctx(v, o):
    return [v / "etc/selinux/vendor_property_contexts", o / "etc/selinux/odm_property_contexts"]


def rewrite(path, text):
    """Write text into the existing file without replacing the inode (keeps xattrs, owner and mode)."""
    with open(path, "r+", encoding="utf-8", errors="surrogateescape") as fh:
        fh.write(text)
        fh.truncate()


def read(path):
    return path.read_text(encoding="utf-8", errors="surrogateescape")


def decls(paths):
    out = {}
    for p in paths:
        if p.exists():
            for kind, name in DECL.findall(read(p)):
                out.setdefault(name, set()).add(kind)
    return out


def decl_conflicts(s, v, o):
    system, vendor = decls(system_cils(s)), decls(vendor_cils(v, o))
    return {n: (system[n], vendor[n]) for n in sorted(set(system) & set(vendor))}


def propctx_entries(paths):
    """{(name, match kind): {(context, type)}} for every entry of the given property_contexts files."""
    out = {}
    for p in paths:
        if not p.exists():
            continue
        for line in read(p).splitlines():
            parts = line.split("#", 1)[0].split()
            if len(parts) < 2:
                continue
            match = parts[2] if len(parts) > 2 else "prefix"
            typ = " ".join(parts[3:]) if len(parts) > 3 else ""
            out.setdefault((parts[0], match), set()).add((parts[1], typ))
    return out


def propctx_conflicts(s, v, o):
    system, vendor = propctx_entries(system_propctx(s)), propctx_entries(vendor_propctx(v, o))
    out = {}
    for key in sorted(set(system) & set(vendor)):
        # The context decides the trie build; the optional type only matters when both sides give one.
        sc, vc = {c for c, _ in system[key]}, {c for c, _ in vendor[key]}
        st, vt = {t for _, t in system[key] if t}, {t for _, t in vendor[key] if t}
        if sc != vc or (st and vt and st != vt):
            out[key] = (system[key], vendor[key])
    return out


def simple_ctx(paths):
    out = {}
    for p in paths:
        if not p.exists():
            continue
        for line in read(p).splitlines():
            parts = line.split("#", 1)[0].split()
            if len(parts) >= 2:
                out.setdefault(parts[0], set()).add(parts[-1])
    return out


def other_ctx_conflicts(s, v, o):
    total = {}
    for kind in ("service_contexts", "hwservice_contexts", "keystore2_key_contexts"):
        sysf = [s / f"system/etc/selinux/plat_{kind}", s / f"system_ext/etc/selinux/system_ext_{kind}",
                s / f"product/etc/selinux/product_{kind}"]
        venf = [v / f"etc/selinux/vendor_{kind}", o / f"etc/selinux/odm_{kind}"]
        if kind == "service_contexts":
            venf.append(v / "etc/selinux/vndservice_contexts")
        a, b = simple_ctx(sysf), simple_ctx(venf[:2])
        total[kind] = {k: (a[k], b[k]) for k in sorted(set(a) & set(b)) if a[k] != b[k]}
    return total


def cmd_conflicts(s, v, o):
    c = decl_conflicts(s, v, o)
    for n, (a, b) in c.items():
        print(f"{n}: system {sorted(a)} vendor {sorted(b)}")
    print(f"{len(c)} conflicting declarations")
    return 1 if c else 0


def cmd_propctx(s, v, o):
    c = propctx_conflicts(s, v, o)
    for (name, match), (a, b) in c.items():
        print(f"{name} ({match}): system {sorted(a)} vendor {sorted(b)}")
    print(f"{len(c)} conflicting property_contexts entries")
    return 1 if c else 0


def cmd_contexts(s, v, o):
    n = 0
    for kind, c in other_ctx_conflicts(s, v, o).items():
        for name, (a, b) in c.items():
            print(f"{kind}: {name}: system {sorted(a)} vendor {sorted(b)}")
        print(f"{kind}: {len(c)} conflicting entries")
        n += len(c)
    return 1 if n else 0


def cmd_fix_system(s, v, o):
    for p in system_cils(s):
        if not p.exists():
            continue
        text = read(p)
        new, n = XPERM_LOG.subn("", text)
        if n:
            rewrite(p, new)
        print(f"{p.name}: removed {n} auditallowx/dontauditx rules")
    conflicts = propctx_conflicts(s, v, o)
    for p in system_propctx(s):
        if not p.exists():
            continue
        keep, dropped = [], []
        for line in read(p).splitlines(keepends=True):
            parts = line.split("#", 1)[0].split()
            if len(parts) >= 2 and (parts[0], parts[2] if len(parts) > 2 else "prefix") in conflicts:
                dropped.append(parts[0])
                continue
            keep.append(line)
        if dropped:
            rewrite(p, "".join(keep))
            print(f"{p.name}: dropped {dropped} (the vendor's definition is kept)")
    left = propctx_conflicts(s, v, o)
    print(f"property_contexts conflicts left: {len(left)}")
    return 1 if left else 0


def cmd_fix_vendor(s, v, o):
    rename, drop, unhandled = {}, [], []
    for name, (sk, vk) in decl_conflicts(s, v, o).items():
        if sk == {"typeattribute"} and vk == {"type"}:
            rename[name] = "base_" + name      # vendor HAL domain vs a ColorOS attribute of the same name
        elif sk == {"type"} and vk == {"type"}:
            drop.append(name)                  # same type on both sides: use the system's declaration
        else:
            unhandled.append((name, sk, vk))
    print(f"rename in vendor policy: {sorted(rename)}\ndrop from vendor policy: {drop}")
    for name, sk, vk in unhandled:
        print(f"UNHANDLED conflict {name}: system {sorted(sk)} vendor {sorted(vk)}")
    word = re.compile(r"(?<![\w.])(" + "|".join(map(re.escape, rename)) + r")(?![\w.])") if rename else None
    for d in (v / "etc/selinux", o / "etc/selinux"):
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if not p.is_file() or not (p.suffix == ".cil" or "contexts" in p.name):
                continue
            text = read(p)
            new = word.sub(lambda m: rename[m.group(1)], text) if word else text
            if p.name in ("vendor_sepolicy.cil", "odm_sepolicy.cil"):
                for t in drop:
                    new = re.sub(rf"^\(type {re.escape(t)}\)\n", "", new, flags=re.M)
                    new = re.sub(rf"^\(roletype object_r {re.escape(t)}\)\n", "", new, flags=re.M)
            if p.name == "plat_pub_versioned.cil":
                new, n = XPERM_ALL.subn("", new)
                print(f"{p.name}: removed {n} extended-permission rules")
            if new != text:
                rewrite(p, new)
                print(f"patched {p}")
    return 1 if unhandled else 0


def main():
    cmds = {"conflicts": cmd_conflicts, "propctx": cmd_propctx, "contexts": cmd_contexts,
            "fix-system": cmd_fix_system, "fix-vendor": cmd_fix_vendor}
    if len(sys.argv) != 5 or sys.argv[1] not in cmds:
        sys.exit(__doc__)
    sys.exit(cmds[sys.argv[1]](*map(Path, sys.argv[2:5])))


if __name__ == "__main__":
    main()
