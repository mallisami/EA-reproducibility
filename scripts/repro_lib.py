"""Shared helpers for the L0-L2 reproduction scripts (Python 3.12, standard library).

All paths are resolved relative to the package root (the parent of scripts/), so the scripts can be run
from any working directory.
"""
import csv
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXP = ROOT / 'experiments'
CODE = ROOT / 'code'
OUT = ROOT / 'outputs'

NUM_RE = re.compile(r'[−-]?\d[\d,]*(?:\.\d+)?')


def sha256_file(p, bufsize=1 << 20):
    h = hashlib.sha256()
    with open(p, 'rb') as fh:
        while True:
            b = fh.read(bufsize)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


class Log:
    """Tee to stdout and a log file; records elapsed time."""

    def __init__(self, path):
        self.t0 = time.time()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(path, 'w', encoding='utf-8')

    def __call__(self, *a):
        msg = ' '.join(str(x) for x in a)
        line = '[%7.1fs] %s' % (time.time() - self.t0, msg)
        print(line, flush=True)
        self.fh.write(line + '\n'); self.fh.flush()

    def elapsed(self):
        return time.time() - self.t0


