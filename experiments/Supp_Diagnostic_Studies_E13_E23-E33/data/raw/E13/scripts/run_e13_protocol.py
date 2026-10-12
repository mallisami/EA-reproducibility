#!/usr/bin/env python3
from pathlib import Path
import json,subprocess,sys,argparse
ROOT=Path(__file__).resolve().parents[1];RES=ROOT/'results'
def run_attempt(n):return subprocess.run([sys.executable,str(ROOT/'scripts/run_e13_once.py'),'--attempt',str(n)]).returncode
def status(n):
 p=RES/f'e13_live_attempt_{n}.json';return json.loads(p.read_text())['status'] if p.exists() else None
def preflight():
 p=subprocess.run([sys.executable,str(ROOT/'scripts/preflight_e13.py')],capture_output=True,text=True)
 try:rec=json.loads(p.stdout)
 except Exception:rec={'status':'INFRA_FAILURE','reason':'preflight did not return valid JSON','stdout':p.stdout,'stderr':p.stderr}
 print(json.dumps(rec,indent=2));return rec

def main():
 ap=argparse.ArgumentParser(description='Execute the frozen E13 protocol once after a passing cluster preflight.')
 ap.add_argument('--execute',action='store_true',help='required explicit acknowledgement to start live attempt 1')
 a=ap.parse_args()
 if not a.execute:raise SystemExit('No experiment started. Re-run with --execute only after preflight passes.')
 if (RES/'e13_live_attempt_1.json').exists():raise SystemExit('attempt 1 already exists; protocol refuses restart')
 pf=preflight()
 if pf.get('status')!='PASS':raise SystemExit('E13 preflight is not PASS; no live attempt file was created')
 s=None;run_attempt(1);s=status(1)
 if s=='INFRA_FAILURE':
  run_attempt(2);s2=status(2);print('attempt2 status',s2);raise SystemExit(0 if s2=='PASS' else 2)
 print('attempt1 status',s);raise SystemExit(0 if s=='PASS' else 3)
if __name__=='__main__':main()
