#!/usr/bin/env bash
set -euo pipefail
cd /workspace/PACBench
export UV_CACHE_DIR=/workspace/.uv-cache
export UV_PYTHON_INSTALL_DIR=/workspace/.uv-python
python3 -m venv /workspace/pacbench-venv
uv pip install --python /workspace/pacbench-venv/bin/python pytest==9.1.1 pytypedstream==0.1.0 hindsight-api==0.10.2
if [ ! -d /workspace/memcal/.git ]; then
  git clone --no-checkout https://github.com/cuuush/memcal.git /workspace/memcal
  git -C /workspace/memcal checkout --detach babc16668a001d5f52ceb8bdf9fcf608b26a7ab5
fi
uv pip install --python /workspace/pacbench-venv/bin/python -e /workspace/memcal
if [ ! -d vendor/hermes-isolated/.git ]; then
  git init vendor/hermes-isolated
  git -C vendor/hermes-isolated remote add origin https://github.com/NousResearch/hermes-agent.git
  git -C vendor/hermes-isolated fetch --depth 1 origin 0348a190d9a317d80853618ea018927287c6b1d7
  git -C vendor/hermes-isolated checkout --detach FETCH_HEAD
fi
test "$(git -C vendor/hermes-isolated rev-parse HEAD)" = 0348a190d9a317d80853618ea018927287c6b1d7
git -C vendor/hermes-isolated config core.abbrev 7
if git -C vendor/hermes-isolated apply --reverse --check ../../patches/hermes-isolated.diff 2>/dev/null; then
  :
else
  git -C vendor/hermes-isolated apply --check ../../patches/hermes-isolated.diff
  git -C vendor/hermes-isolated apply ../../patches/hermes-isolated.diff
fi
UV_PROJECT_ENVIRONMENT=/workspace/PACBench/vendor/venv-hermes-isolated uv sync --python /workspace/pacbench-venv/bin/python --frozen --no-dev --project vendor/hermes-isolated
uv pip install --python vendor/venv-hermes-isolated/bin/python -r vendor/hermes-isolated-extra-requirements.txt
node --check explorer/dashboard.js
/workspace/pacbench-venv/bin/python scripts/build_explorer_data.py
HINDSIGHT_API_LLM_API_KEY=offline-test-sentinel PACBENCH_PYTHON=/workspace/pacbench-venv/bin/python /workspace/pacbench-venv/bin/python -m pytest -q
