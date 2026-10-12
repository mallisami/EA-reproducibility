#!/usr/bin/env python3
"""Fill and verify the E39 lock (sha256 of every code/config file + design constants).

Extension of the E38v2 protocol/lock_hash.py (same digest, glob and pinned semantics). E39 changes:
  * ONE lock covers all sub-experiments A-G (design block "subexperiments").
  * Forbidden seeds: 20260912 (E38), 1772720255 (first E38v2 run) and the E38v2-RERUN seed, which fill
    reads from the rerun LOCK (--e38v2r-lock) or takes from --forbid-seed; fill refuses without it.
  * add-stage2 accepts only a stage-2 schedule whose trials are all E39-A (top-up applies to E39-A only)
    and whose trial ids are >= 100001.

  fill   : python3 protocol/lock_hash_e39.py fill protocol/LOCK_TEMPLATE_E39.json protocol/LOCK_E39.json \
               --seed <int> --run-id <id> --schedule results/<id>/SCHEDULE.json \
               --analysis-plan protocol/ANALYSIS_PLAN_E39.txt \
               (--e38v2r-lock ~/e38v2r_20260926/protocol/LOCK_E38v2.json | --forbid-seed <int>) [--probe-tools]
  verify : python3 protocol/lock_hash_e39.py verify protocol/LOCK_E39.json [--allow-missing] [--require-signed]
  add-stage2 : python3 protocol/lock_hash_e39.py add-stage2 protocol/LOCK_E39.json \
               --schedule2 results/<id>/SCHEDULE_STAGE2.json [--topup results/<id>/stage2/stage2_cells_E39.json]

lock_sha256 = sha256 of the canonical JSON of the lock with lock_sha256=null and "signers" removed.
--probe-tools records image IDs via read-only `docker image inspect` (no pulls, no cluster access).
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import glob
import hashlib
import json
import os
import subprocess
import sys

FORBIDDEN_DEFAULT = (20260912, 1772720255, 154080643)   # E38, E38v2 first run, E38v2 rerun
STAGE2_MIN_TRIAL_ID = 100001


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def lock_digest(lock: dict) -> str:
    x = copy.deepcopy(lock)
    x["lock_sha256"] = None
    x.pop("signers", None)
    return hashlib.sha256(canonical(x)).hexdigest()


def resolve_root(lock: dict, root_arg):
    root = os.path.abspath(os.path.expanduser(root_arg or lock.get("root") or "."))
    if not os.path.isdir(root):
        raise SystemExit("root is not a directory: " + root)
    return root


def expand_glob(root: str, pattern: str):
    return sorted(os.path.relpath(p, root).replace(os.sep, "/")
                  for p in glob.glob(os.path.join(root, pattern)) if os.path.isfile(p))


def forbidden_set(design: dict):
    s = set(FORBIDDEN_DEFAULT)
    for x in design.get("forbidden_schedule_seeds") or []:
        if isinstance(x, int):
            s.add(x)
    return s


def check_seed(design: dict, errors, required: bool) -> None:
    seed = design.get("schedule_seed")
    if seed is None:
        if required:
            errors.append("schedule_seed is null")
        return
    if type(seed) is not int or seed <= 0:
        errors.append("schedule_seed must be a positive int")
    elif seed in forbidden_set(design):
        errors.append("schedule_seed %d is forbidden (reuse of an E38/E38v2 seed)" % seed)
    if required and not design.get("e38v2r_seed_recorded"):
        errors.append("E38v2-rerun seed not recorded in forbidden_schedule_seeds (use --e38v2r-lock or --forbid-seed)")


def probe_images(env: dict) -> None:
    for key, image in (("node_image_id", env.get("node_image")), ("pause_image_id", env.get("pause_image"))):
        if not image:
            continue
        try:
            out = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", image],
                                 capture_output=True, text=True, timeout=30)
            env[key] = out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None
        except (OSError, subprocess.SubprocessError):
            env[key] = None


def cmd_fill(a) -> int:
    with open(a.template, encoding="utf-8") as fh:
        lock = json.load(fh)
    if os.path.exists(a.out):
        raise SystemExit("refusing to overwrite existing lock " + a.out)
    root = resolve_root(lock, a.root)
    errors = []
    files = []
    for entry in lock.get("files", []):
        if "glob" in entry:
            matches = expand_glob(root, entry["glob"])
            if not matches:
                (files.append(dict(entry, missing=True)) if a.allow_missing
                 else errors.append("glob matched nothing: " + entry["glob"]))
            for p in matches:
                files.append({"path": p, "role": entry.get("role"), "from_glob": entry["glob"],
                              "sha256": sha256_file(os.path.join(root, p))})
            continue
        path = os.path.join(root, entry["path"])
        if os.path.isfile(path):
            files.append(dict(entry, sha256=sha256_file(path)))
        elif a.allow_missing:
            files.append(dict(entry, sha256=None, missing=True))
        else:
            errors.append("missing file: " + entry["path"])
    lock["files"] = files
    for p in lock.get("pinned", []):
        path = os.path.join(root, p["path"])
        if not os.path.isfile(path):
            errors.append("missing pinned file: " + p["path"])
            continue
        p["sha256"] = sha256_file(path)
        if p["sha256"] != p["expected_sha256"]:
            errors.append("pinned hash mismatch: %s" % p["path"])
    design = lock.setdefault("design", {})
    forb = [x for x in (design.get("forbidden_schedule_seeds") or []) if isinstance(x, int)]
    extra = list(a.forbid_seed or [])
    if a.e38v2r_lock:
        try:
            with open(os.path.expanduser(a.e38v2r_lock), encoding="utf-8") as fh:
                rl = json.load(fh)
            rs = rl.get("schedule_seed", (rl.get("design") or {}).get("schedule_seed"))
            if not isinstance(rs, int):
                errors.append("E38v2-rerun LOCK has no integer schedule_seed")
            else:
                extra.append(rs)
                design["e38v2r_lock_sha256"] = sha256_file(os.path.expanduser(a.e38v2r_lock))
        except (OSError, ValueError) as exc:
            errors.append("E38v2-rerun LOCK unreadable: %r" % (exc,))
    if extra:
        design["e38v2r_seed_recorded"] = True
    design["forbidden_schedule_seeds"] = sorted(set(forb) | set(FORBIDDEN_DEFAULT) | set(extra))
    if a.seed is not None:
        design["schedule_seed"] = a.seed
    check_seed(design, errors, required=not a.allow_missing)
    lock["schedule_seed"] = design.get("schedule_seed")
    if a.run_id:
        lock["run_id"] = a.run_id
    for key, rel in (("schedule", a.schedule), ("analysis_plan", a.analysis_plan)):
        if not rel:
            continue
        p = rel if os.path.isabs(rel) else os.path.join(root, rel)
        if not os.path.isfile(p):
            errors.append("missing %s file: %s" % (key, rel))
            continue
        lock[key + "_path"] = os.path.relpath(os.path.abspath(p), root).replace(os.sep, "/")
        lock[key + "_sha256"] = sha256_file(p)
    if a.schedule and lock.get("schedule_path"):
        try:
            with open(os.path.join(root, lock["schedule_path"]), encoding="utf-8") as fh:
                sch = json.load(fh)
            if isinstance(sch, dict):
                if sch.get("schedule_seed") != design.get("schedule_seed"):
                    errors.append("schedule seed %r != lock seed %r" % (sch.get("schedule_seed"), design.get("schedule_seed")))
                if a.run_id and sch.get("run_id") != a.run_id:
                    errors.append("schedule run_id %r != --run-id %r" % (sch.get("run_id"), a.run_id))
                exps = sorted({str(t.get("experiment")) for t in sch.get("trials") or []})
                lock["schedule_experiments"] = exps
                if any(t.get("stage") != 1 for t in sch.get("trials") or []):
                    errors.append("stage-1 schedule contains non-stage-1 trials")
        except (OSError, ValueError) as exc:
            errors.append("schedule unreadable: %r" % (exc,))
    if a.probe_tools:
        probe_images(lock.setdefault("environment", {}))
    if errors:
        for e in errors:
            print("ERROR " + e, file=sys.stderr)
        return 1
    lock["root"] = root
    lock["created_utc"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lock["status"] = "LOCKED-" + lock["created_utc"]
    lock["lock_sha256"] = lock_digest(lock)
    with open(a.out, "x", encoding="utf-8") as fh:
        json.dump(lock, fh, indent=2, sort_keys=False)
        fh.write("\n")
    print("wrote %s: %d files, lock_sha256=%s" % (a.out, len(files), lock["lock_sha256"]))
    return 0


def cmd_verify(a) -> int:
    with open(a.lock, encoding="utf-8") as fh:
        lock = json.load(fh)
    root = resolve_root(lock, a.root)
    errors = []
    ok = 0
    listed = set()
    for f in lock.get("files", []):
        if f.get("missing"):
            if not a.allow_missing:
                errors.append("lock records missing file: " + (f.get("path") or f.get("glob")))
            continue
        listed.add(f["path"])
        path = os.path.join(root, f["path"])
        if not os.path.isfile(path):
            errors.append("file vanished: " + f["path"])
        elif sha256_file(path) != f["sha256"]:
            errors.append("hash mismatch: " + f["path"])
        else:
            ok += 1
    for pattern in sorted({f["from_glob"] for f in lock.get("files", []) if f.get("from_glob")}):
        for p in expand_glob(root, pattern):
            if p not in listed:
                errors.append("unlocked file matches %s: %s" % (pattern, p))
    for p in lock.get("pinned", []):
        path = os.path.join(root, p["path"])
        if not os.path.isfile(path) or sha256_file(path) != p["expected_sha256"] or p.get("sha256") != p["expected_sha256"]:
            errors.append("pinned file mismatch: " + p["path"])
        else:
            ok += 1
    check_seed(lock.get("design", {}), errors, required=not a.allow_missing)
    if "schedule_seed" in lock and lock.get("schedule_seed") != lock.get("design", {}).get("schedule_seed"):
        errors.append("top-level schedule_seed != design.schedule_seed")
    for key in ("schedule", "analysis_plan", "stage2_schedule", "stage2_topup"):
        rel, h = lock.get(key + "_path"), lock.get(key + "_sha256")
        if not h:
            continue
        p = os.path.join(root, rel) if rel else None
        if not p or not os.path.isfile(p):
            errors.append("%s file missing: %s" % (key, rel))
        elif sha256_file(p) != h:
            errors.append("hash mismatch: %s (%s)" % (key, rel))
        else:
            ok += 1
    if lock.get("status") is not None and not str(lock.get("status")).startswith("LOCKED"):
        errors.append("status %r does not start with LOCKED" % lock.get("status"))
    if lock.get("lock_sha256") != lock_digest(lock):
        errors.append("lock_sha256 mismatch: lock content edited after fill")
    if a.require_signed:
        signers = lock.get("signers") or []
        if not signers or any(not s.get("name") or not s.get("signed_utc") for s in signers):
            errors.append("lock not signed by all signer slots")
    for e in errors:
        print("FAIL " + e)
    print("%s: %d hashes verified, %d problem(s), lock_sha256=%s" % ("OK" if not errors else "FAILED", ok, len(errors),
                                                                       lock.get("lock_sha256")))
    return 1 if errors else 0


def cmd_add_stage2(a) -> int:
    rc = cmd_verify(argparse.Namespace(lock=a.lock, root=a.root, allow_missing=False, require_signed=False))
    if rc != 0:
        print("ERROR lock does not verify; stage-2 hash not added", file=sys.stderr)
        return 1
    with open(a.lock, encoding="utf-8") as fh:
        lock = json.load(fh)
    if lock.get("stage2_schedule_sha256"):
        print("ERROR stage-2 schedule already recorded", file=sys.stderr)
        return 1
    root = resolve_root(lock, a.root)
    p = a.schedule2 if os.path.isabs(a.schedule2) else os.path.join(root, a.schedule2)
    if not os.path.isfile(p):
        print("ERROR missing stage-2 schedule " + a.schedule2, file=sys.stderr)
        return 1
    with open(p, encoding="utf-8") as fh:
        sch = json.load(fh)
    seed = sch.get("schedule_seed") if isinstance(sch, dict) else None
    if seed != lock.get("schedule_seed"):
        print("ERROR stage-2 schedule seed %r != lock seed %r" % (seed, lock.get("schedule_seed")), file=sys.stderr)
        return 1
    if lock.get("run_id") and sch.get("run_id") != lock.get("run_id"):
        print("ERROR stage-2 schedule run_id mismatch", file=sys.stderr)
        return 1
    trials = sch.get("trials") or []
    bad = [t.get("trial_id") for t in trials if str(t.get("experiment")).upper() not in ("A", "E39-A")]
    if bad:
        print("ERROR stage-2 schedule contains non-E39-A trials (e.g. %s); top-up applies to E39-A only" % bad[:5], file=sys.stderr)
        return 1
    low = [t.get("trial_id") for t in trials if not isinstance(t.get("trial_id"), int) or t["trial_id"] < STAGE2_MIN_TRIAL_ID]
    if low:
        print("ERROR stage-2 trial ids must be >= %d (e.g. %s)" % (STAGE2_MIN_TRIAL_ID, low[:5]), file=sys.stderr)
        return 1
    lock["lock_sha256_stage1"] = lock.get("lock_sha256")
    lock["stage2_schedule_path"] = os.path.relpath(os.path.abspath(p), root).replace(os.sep, "/")
    lock["stage2_schedule_sha256"] = sha256_file(p)
    lock["stage2_schedule_seed"] = seed
    if a.topup:
        tp = a.topup if os.path.isabs(a.topup) else os.path.join(root, a.topup)
        lock["stage2_topup_path"] = os.path.relpath(os.path.abspath(tp), root).replace(os.sep, "/")
        lock["stage2_topup_sha256"] = sha256_file(tp)
    lock["stage2_added_utc"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lock["lock_sha256"] = lock_digest(lock)
    tmp = a.lock + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(lock, fh, indent=2, sort_keys=False)
        fh.write("\n")
    os.replace(tmp, a.lock)
    print("stage-2 schedule recorded: %s sha256=%s; lock_sha256=%s" % (lock["stage2_schedule_path"],
                                                                       lock["stage2_schedule_sha256"], lock["lock_sha256"]))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fill")
    f.add_argument("template")
    f.add_argument("out")
    f.add_argument("--root")
    f.add_argument("--seed", type=int)
    f.add_argument("--run-id")
    f.add_argument("--schedule")
    f.add_argument("--analysis-plan")
    f.add_argument("--e38v2r-lock", help="E38v2-rerun LOCK; its schedule_seed becomes forbidden")
    f.add_argument("--forbid-seed", type=int, action="append", help="additional forbidden seed (E38v2-rerun seed)")
    f.add_argument("--probe-tools", action="store_true")
    f.add_argument("--allow-missing", action="store_true")
    v = sub.add_parser("verify")
    v.add_argument("lock")
    v.add_argument("--root")
    v.add_argument("--allow-missing", action="store_true")
    v.add_argument("--require-signed", action="store_true")
    s2 = sub.add_parser("add-stage2")
    s2.add_argument("lock")
    s2.add_argument("--schedule2", required=True)
    s2.add_argument("--topup")
    s2.add_argument("--root")
    a = ap.parse_args(argv)
    if a.cmd == "fill":
        return cmd_fill(a)
    if a.cmd == "add-stage2":
        return cmd_add_stage2(a)
    return cmd_verify(a)


if __name__ == "__main__":
    sys.exit(main())
