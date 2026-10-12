#!/bin/bash
cd ~/e38v2_20260926
find src webhook deploy protocol analysis -type f -not -path '*/__pycache__/*' -not -path '*/.pytest_cache/*' | sort | xargs sha256sum | awk '{print substr($1,1,16), $2}'
