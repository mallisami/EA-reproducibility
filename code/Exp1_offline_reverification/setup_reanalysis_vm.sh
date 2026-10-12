set -e
cd ~/dcji_reanalysis_20260926/inputs
sha256sum frozen_inputs.tgz e38_inputs.tgz
mkdir -p frozen e38code e38raw
tar -xzf frozen_inputs.tgz -C frozen
tar -xzf e38_inputs.tgz -C e38code
cp -a ~/e38/EXECUTE_ON_AZURE/results/e3820260919094024-smoke/. e38raw/
sha256sum e38raw/RAW_TRIALS.jsonl
chmod -R a-w frozen e38code e38raw
du -sh frozen e38code e38raw
cd ~/dcji_reanalysis_20260926
python3 -m venv venv
./venv/bin/pip install -q numpy scipy pandas 2>&1 | tail -2
./venv/bin/python -c "import numpy, scipy, pandas; print(numpy.__version__, scipy.__version__, pandas.__version__)"