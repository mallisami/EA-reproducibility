#!/bin/bash
# Start / stop / status of the pod-event recorder (src/pod_watch.py) for one run namespace.
# Usage: podwatch.sh start|stop|status RUN_ID
set -uo pipefail
CMD=${1:?start|stop|status}; RUN_ID=${2:?run id}
E=~/e38v2r_20260926
R=$E/results/$RUN_ID
T=$E/.tokens_$RUN_ID
PIDF=$R/.podwatch.pid
mkdir -p $R
case $CMD in
start)
  if [ -f $PIDF ] && kill -0 $(cat $PIDF) 2>/dev/null; then echo "already running pid $(cat $PIDF)"; exit 0; fi
  rm -f $R/PODWATCH_STOP
  cd $E/src
  setsid nohup $E/venv_runner/bin/python pod_watch.py --api-server "$(cat $T/server.txt)" --ca-file $T/ca.crt \
     --token-file $T/reset.token --namespace e38v2-$RUN_ID --out $R/pod_events.jsonl --stop-file $R/PODWATCH_STOP \
     > $R/podwatch_stdout.log 2>&1 < /dev/null &
  echo $! > $PIDF
  sleep 2
  echo "podwatch pid $(cat $PIDF)"; head -2 $R/pod_events.jsonl ;;
stop)
  touch $R/PODWATCH_STOP
  if [ -f $PIDF ]; then kill -TERM $(cat $PIDF) 2>/dev/null; for i in $(seq 1 90); do kill -0 $(cat $PIDF) 2>/dev/null || break; sleep 1; done; fi
  kill -0 $(cat $PIDF) 2>/dev/null && echo "podwatch still running" || echo "podwatch stopped"
  tail -1 $R/pod_events.jsonl; rm -f $PIDF ;;
status)
  if [ -f $PIDF ] && kill -0 $(cat $PIDF) 2>/dev/null; then echo "running pid $(cat $PIDF)"; else echo "NOT running"; fi
  wc -l $R/pod_events.jsonl; grep -c '"type":"GAP"' $R/pod_events.jsonl; grep -c '"type":"ERROR"' $R/pod_events.jsonl ;;
esac
