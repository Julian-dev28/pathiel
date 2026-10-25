#!/bin/sh
# Shared container entrypoint for every pathiel process — web, rotator
# (see k8s/statefulset.yaml). Both run from this one image; this only
# bootstraps what has to exist on the mounted volume before ANY of them run,
# then execs the process's real command unchanged.
set -e

# The persistent volume is mounted fresh (or restored) at /data by k8s (or
# `docker run -v`) at container start — anything baked at build time under
# /data is gone by the time this runs. PATHIEL_STATE_DIR (/data/.state) must
# exist before anything can write a state file through it
# (pathiel/state.py:state_file). Cheap and idempotent, so it runs
# unconditionally.
mkdir -p /data/.state

exec "$@"
