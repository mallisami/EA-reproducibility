#!/usr/bin/env python3
"""Small lock verifier shared by E41/E42 live runners."""
from __future__ import annotations
import hashlib, json
from pathlib import Path


def sha256_file(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1<<20),b''):
            h.update(b)
    return h.hexdigest()


def verify_lock(lock_path, root, schedule_path, run_id, seed):
    root=Path(root).resolve(); lock_path=Path(lock_path); schedule_path=Path(schedule_path)
    lock=json.loads(lock_path.read_text(encoding='utf-8'))
    errs=[]
    if not str(lock.get('status','')).startswith('LOCKED'):
        errs.append('status is not LOCKED')
    if lock.get('run_id') != run_id:
        errs.append(f"run_id mismatch lock={lock.get('run_id')!r} requested={run_id!r}")
    if int(lock.get('schedule_seed',-1)) != int(seed):
        errs.append(f"seed mismatch lock={lock.get('schedule_seed')} schedule={seed}")
    got=sha256_file(schedule_path)
    if str(lock.get('schedule_sha256','')).lower()!=got:
        errs.append('schedule hash mismatch')
    files=lock.get('files') or {}
    if not isinstance(files,dict) or not files:
        errs.append('lock has no file map')
    else:
        for rel,want in sorted(files.items()):
            p=root/rel
            if not p.is_file(): errs.append(f'missing locked file: {rel}'); continue
            actual=sha256_file(p)
            if actual.lower()!=str(want).lower(): errs.append(f'hash mismatch: {rel}')
    if errs:
        raise RuntimeError('LOCK verification failed: ' + '; '.join(errs[:20]))
    return lock
