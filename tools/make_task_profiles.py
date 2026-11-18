#!/usr/bin/env python3
"""Build the API-level task-profile override for a kernel that uses schedtune instead of uclamp.

Usage: make_task_profiles.py <ColorOS task_profiles.json> <base ROM task_profiles_<api>.json> <output>

libprocessgroup loads /system/etc/task_profiles.json and then, if present,
/system/etc/task_profiles/task_profiles_<ro.product.first_api_level>.json, whose profiles replace same-named
ones. The base ROM ships such a file for API 28: it moves the standard profiles from the cpu controller to the
schedtune controller, which the 4.14 kernel has (CONFIG_SCHED_TUNE) while it has no uclamp. ColorOS ships no
API-level files, so on this phone nothing would populate /dev/stune and the vendor's schedtune tuning
(init.target.rc) would act on empty groups.

Taking the base ROM's file as is would drop ColorOS's cpu-controller placement, which ColorOS's own profiles
build on. So each overridden profile keeps ColorOS's cgroup joins and gets the base ROM's schedtune action
appended. A profile stops at the first failing action, so the uclamp attribute writes (which cannot succeed
on this kernel) are replaced rather than kept. This is the approach of the OnePlus 7 Pro port.
"""
import json
import sys


def main():
    cos = json.load(open(sys.argv[1]))
    api = json.load(open(sys.argv[2]))
    cos_profiles = {p["Name"]: p for p in cos["Profiles"]}
    out = {"Attributes": api.get("Attributes", []), "Profiles": []}
    for profile in api["Profiles"]:
        name = profile["Name"]
        assert name in cos_profiles, f"ColorOS has no profile named {name}"
        keep = [a for a in cos_profiles[name]["Actions"]
                if a["Name"] == "JoinCgroup" and a["Params"]["Controller"] != "schedtune"]
        out["Profiles"].append({"Name": name, "Actions": keep + profile["Actions"]})
    with open(sys.argv[3], "w") as fh:
        json.dump(out, fh, indent=2)
        fh.write("\n")
    print(f"{sys.argv[3]}: {len(out['Profiles'])} profiles "
          f"({sum(len(p['Actions']) for p in out['Profiles'])} actions), {len(out['Attributes'])} attributes")


if __name__ == "__main__":
    main()
