#!/bin/bash
# run a command detached with its own log. Usage: bg.sh LOGFILE cmd args...
LOG=$1; shift
setsid nohup "$@" > $LOG 2>&1 < /dev/null &
echo "bg pid $! -> $LOG"
