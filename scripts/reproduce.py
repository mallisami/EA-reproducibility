#!/usr/bin/env python3
"""Run the package checks and optional complete fixed-data replay in a stable sequence."""
import argparse, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--full',action='store_true',help='also rerun all scripted analyses from the retained observations')
    args=ap.parse_args()
    scripts=['L0_verify_checksums.py','verify_figure_inputs.py','L2_verify_scope_strengthening_E43_E50.py']
    if args.full:
        scripts+=['L2_rerun_deterministic.py','L2_rerun_analysis.py','L2_rerun_followups.py',
                  'L2_verify_a4d_r2.py','L2_regenerate_scope_strengthening_outputs.py','L2_verify_E52_forced_contest.py']
    scripts+=['make_main_figures.py']
    for name in scripts:
        print('\nRunning '+name,flush=True)
        subprocess.run([sys.executable,str(ROOT/'scripts'/name)],cwd=ROOT,check=True)
    print('\nRequested reproduction steps passed.')
if __name__=='__main__':main()
