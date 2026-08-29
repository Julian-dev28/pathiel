#!/bin/sh
# Shared container entrypoint for every hermes-trader process — web, loop,
# sched, sampler, rotator (see fly.toml [processes] / k8s/statefulset.yaml).
# All five run from this one image; this only bootstraps what has to exist on
# the mounted volume before ANY of them run, then execs the process's real
# command unchanged.
set -e

# The persistent volume is mounted fresh (or restored) at /data by Fly/k8s at
# container start — anything baked at build time under /data is gone by the
# time this runs. `.state` must exist as a REAL directory, not a dangling
# symlink target, before `scripts/scheduler.py` can write through the
# `/app/.state -> /data/.state` symlink created in the Dockerfile (that
# script hardcodes `<repo root>/.state` for its own job-run bookkeeping and
# does not read HERMES_STATE_DIR like the rest of the app does — see the
# Dockerfile's "Runtime state" comment). Cheap and idempotent for every other
# process too, so it runs unconditionally rather than only for `sched`.
mkdir -p /data/.state

exec "$@"
