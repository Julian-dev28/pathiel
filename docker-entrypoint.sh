#!/bin/sh
# Shared container entrypoint for every pathiel process — web, rotator
# (see fly.toml [processes] / k8s/statefulset.yaml). Both run from this one
# image; this only bootstraps what has to exist on the mounted volume before
# ANY of them run, then execs the process's real command unchanged.
set -e

APP_USER=pathiel

# The persistent volume is mounted fresh (or restored) at /data by Fly/k8s at
# container start — anything baked at build time under /data is gone by the
# time this runs. PATHIEL_STATE_DIR (/data/.state) must exist before
# anything can write a state file through it (pathiel/state.py:state_file).
# Cheap and idempotent, so it runs unconditionally.
#
# Never run the app as root (security review 2026-10-25). Two ways in:
#
#   started as root  (docker run, Fly: a Fly volume is mounted root-owned, so
#                    only root can make it writable) -> create the state dir,
#                    hand /data to the app user, drop to it with setpriv.
#   started as the app user (k8s: runAsUser 10001, fsGroup makes the PVC
#                    group-writable) -> just create the state dir.
if [ "$(id -u)" = "0" ]; then
  mkdir -p /data/.state /data/home
  chown -R "$APP_USER:$APP_USER" /data
  exec setpriv --reuid="$APP_USER" --regid="$APP_USER" --init-groups \
    --no-new-privs --inh-caps=-all --bounding-set=-all -- "$@"
fi

if ! mkdir -p /data/.state /data/home 2>/dev/null || [ ! -w /data/.state ]; then
  echo "docker-entrypoint: /data/.state is not writable by uid $(id -u)." >&2
  echo "  k8s: keep securityContext.fsGroup: 10001 on the pod." >&2
  echo "  docker: start as root and this script hands /data to uid 10001." >&2
  exit 1
fi

exec "$@"
