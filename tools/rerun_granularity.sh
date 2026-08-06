#!/usr/bin/env bash
# Re-run the effective-granularity probe now that it computes per-read bytes from the exact byte
# count instead of round-tripping a 2-dp MiB string (which reported 4089 B for a true 4096 B grid).
# Must run from a script file: the probe's pkill pattern matches any command line containing it.
set -u
export NIXL_PY=$HOME/nixl-venv/bin/python
bash "$HOME/projects/gdstrace/tools/probe_effective_granularity.sh" \
  > "$HOME/projects/gdstrace/results/xlayer/effective-granularity.txt" 2>/tmp/gran_err.log
echo "exit=$?"
cat "$HOME/projects/gdstrace/results/xlayer/effective-granularity.txt"
