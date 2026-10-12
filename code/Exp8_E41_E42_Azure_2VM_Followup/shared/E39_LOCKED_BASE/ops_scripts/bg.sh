#!/bin/bash
LOG=$1; shift
setsid nohup $@ > $LOG 2>&1 < /dev/null &
echo bg pid $! '->' $LOG
