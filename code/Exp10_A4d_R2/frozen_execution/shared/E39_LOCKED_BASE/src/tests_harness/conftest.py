import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
for p in (SRC, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
