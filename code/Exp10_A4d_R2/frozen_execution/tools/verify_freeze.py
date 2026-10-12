#!/usr/bin/env python3
from __future__ import annotations
import argparse,hashlib
from pathlib import Path

def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1<<20),b''):h.update(b)
 return h.hexdigest()

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--root',required=True); a=ap.parse_args(); root=Path(a.root).resolve(); mf=root/'FREEZE_MANIFEST.sha256'
 if not mf.is_file(): raise SystemExit('FREEZE_MANIFEST.sha256 missing; run tools/refreeze.py before smoke/main')
 bad=[]; n=0
 for line in mf.read_text(encoding='utf-8').splitlines():
  if not line.strip(): continue
  want,rel=line.split(None,1); rel=rel.strip(); p=root/rel; n+=1
  if not p.is_file(): bad.append(f'MISSING {rel}'); continue
  got=sha(p)
  if got!=want: bad.append(f'HASH {rel} expected={want} actual={got}')
 if bad:
  print('\n'.join(bad[:50])); raise SystemExit(f'FREEZE FAIL: {len(bad)} mismatch(es)')
 print(f'FREEZE PASS: {n} frozen files match')
if __name__=='__main__': main()
