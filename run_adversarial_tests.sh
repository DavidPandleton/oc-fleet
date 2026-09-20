#!/bin/bash
# Run the adversarially-derived regression tests under a wall clock.
# If these hang, the orchestrator poll loop is not making progress, which is
# itself a regression: a run that never terminates strands live sessions.
set -euo pipefail
cd "$(dirname "$0")"
timeout 120 python3 -m pytest test_orchestrator_adversarial.py "$@" -p no:cacheprovider