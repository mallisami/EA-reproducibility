#!/usr/bin/env python3
"""L0 - verify the integrity of the package (Python 3.12 standard library only; < 1 min).

Checks
  1. every file listed in CHECKSUMS.sha256 (package root) exists and has the listed SHA-256;
  2. no unlisted files exist (except files created by the reproduction scripts under outputs/ and
     virtual environments);
  3. the raw export tarballs match provenance/RAW_DATA_HASHES.csv (redacted package copy) and the member-level
     redaction manifests: every member not marked REDACTED/DROPPED/REPACKED_NESTED is byte-identical to the
     original export (member hashes recomputed by streaming the tarballs; skip with --quick).
Usage: python scripts/L0_verify_checksums.py [--quick]
Exit code 0 = all checks pass. Report: outputs/L0/L0_REPORT.txt
"""
import argparse
import csv
import hashlib
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from repro_lib import ROOT, OUT, Log, sha256_file

IGNORE_PREFIXES = ('outputs/', '.venv/', 'venv/', 'scripts/__pycache__/')
IGNORE_NAMES = {'CHECKSUMS.sha256', '.DS_Store', 'Thumbs.db'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--quick', action='store_true', help='skip the member-level check of the raw tarballs')
    a = ap.parse_args()
    log = Log(OUT / 'L0' / 'L0.log')
    log('package root:', ROOT, '| python', sys.version.split()[0])
    if sys.version_info[:2] != (3, 12):
        log('NOTE: written and tested for Python 3.12; running on', sys.version.split()[0])
    listed = {}
    with open(ROOT / 'CHECKSUMS.sha256', encoding='utf-8') as fh:
        for line in fh:
            line = line.rstrip('\n')
            if not line or line.startswith('#'):
                continue
            h, rel = line.split('  ', 1)
            listed[rel] = h
    log('CHECKSUMS.sha256 lists %d files' % len(listed))
    bad, missing = [], []
    for i, (rel, h) in enumerate(sorted(listed.items())):
        p = ROOT / rel
        if not p.is_file():
            missing.append(rel); continue
        if sha256_file(p) != h:
            bad.append(rel)
        if (i + 1) % 1000 == 0:
            log('  hashed %d files' % (i + 1))
    present = set()
    for p in ROOT.rglob('*'):
        if p.is_file():
            rel = p.relative_to(ROOT).as_posix()
            if rel in IGNORE_NAMES or rel.startswith(IGNORE_PREFIXES) or '/__pycache__/' in rel:
                continue
            present.add(rel)
    unlisted = sorted(present - set(listed))
    log('files: %d listed, %d hash mismatches, %d missing, %d unlisted' % (len(listed), len(bad), len(missing), len(unlisted)))
    for x in bad[:20]:
        log('  MISMATCH', x)
    for x in missing[:20]:
        log('  MISSING', x)
    for x in unlisted[:20]:
        log('  UNLISTED', x)

    # raw tarballs
    raw_ok = True
    with open(ROOT / 'provenance' / 'RAW_DATA_HASHES.csv', newline='', encoding='utf-8') as fh:
        raws = list(csv.DictReader(fh))
    for r in raws:
        p = ROOT / r['package_path']
        got = sha256_file(p) if p.is_file() else None
        ok = got == r['package_sha256']
        raw_ok &= ok
        log('raw %-100s %s' % (r['package_path'], 'OK' if ok else 'FAIL'))
        if a.quick or not ok or not r.get('redaction_manifest'):
            continue
        man = {}
        with open(ROOT / r['redaction_manifest'], newline='', encoding='utf-8') as fh:
            for m in csv.DictReader(fh):
                man[m['member']] = m
        n_same = n_red = n_fail = 0
        with tarfile.open(p, 'r:gz') as tf:
            for m in tf:
                if not m.isfile():
                    continue
                h = hashlib.sha256()
                f = tf.extractfile(m)
                while True:
                    b = f.read(1 << 20)
                    if not b:
                        break
                    h.update(b)
                e = man.get(m.name)
                if e is None:
                    n_fail += 1; continue
                if e['action'] == 'UNCHANGED':
                    if h.hexdigest() == e['original_sha256']:
                        n_same += 1
                    else:
                        n_fail += 1
                else:
                    n_red += 1 if h.hexdigest() == e['redacted_package_sha256'] else 0
                    n_fail += 0 if h.hexdigest() == e['redacted_package_sha256'] else 1
        dropped = sum(1 for e in man.values() if e['action'] == 'DROPPED')
        log('   members: %d byte-identical to the original export, %d redacted (as documented), %d dropped, %d failures'
            % (n_same, n_red, dropped, n_fail))
        raw_ok &= n_fail == 0
    ok = not bad and not missing and not unlisted and raw_ok
    (OUT / 'L0').mkdir(parents=True, exist_ok=True)
    with open(OUT / 'L0' / 'L0_REPORT.txt', 'w', encoding='utf-8') as fh:
        fh.write('L0 %s: %d files listed, %d mismatches, %d missing, %d unlisted, raw tarballs %s, %.1f s\n' % (
            'PASS' if ok else 'FAIL', len(listed), len(bad), len(missing), len(unlisted), 'OK' if raw_ok else 'FAIL', log.elapsed()))
        for x in bad:
            fh.write('MISMATCH %s\n' % x)
        for x in missing:
            fh.write('MISSING %s\n' % x)
        for x in unlisted:
            fh.write('UNLISTED %s\n' % x)
    log('L0 %s in %.1f s' % ('PASS' if ok else 'FAIL', log.elapsed()))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
