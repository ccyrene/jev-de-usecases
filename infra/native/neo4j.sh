#!/usr/bin/env bash
# Neo4j Community 5.26.32 (tarball, Java 21), bound to 127.0.0.1. No enterprise features.
# Usage: neo4j.sh {install|start|stop|status}
set -euo pipefail

VERSION="5.26.32"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${DEBENCH_ENV_FILE:-$ROOT_DIR/.env}"
BASE="${DEBENCH_CACHE:-$HOME/.cache/de-jev}/neo4j"
HOME_DIR="$BASE/neo4j-community-$VERSION"
URL="https://dist.neo4j.org/neo4j-community-$VERSION-unix.tar.gz"
START_TIMEOUT="${NEO4J_START_TIMEOUT:-180}"

# Read a non-secret setting from the environment, then .env, then a default.
setting() {
  local key="$1" default="$2" val="${!1:-}"
  if [[ -z "$val" && -f "$ENV_FILE" ]]; then
    val="$(grep -E "^$key=" "$ENV_FILE" | tail -n1 | cut -d= -f2- | tr -d '\r"' || true)"
  fi
  echo "${val:-$default}"
}

HTTP_PORT="$(setting NEO4J_HTTP_PORT 7474)"
BOLT_PORT="$(setting NEO4J_BOLT_PORT 7687)"

is_up() { (exec 3<>"/dev/tcp/127.0.0.1/$BOLT_PORT") 2>/dev/null; }

install() {
  if [[ ! -x "$HOME_DIR/bin/neo4j" ]]; then
    mkdir -p "$BASE"
    local tgz="$BASE/neo4j-community-$VERSION-unix.tar.gz"
    if [[ ! -s "$tgz" ]]; then
      curl -fL --retry 3 -C - -o "$tgz.part" "$URL"
      mv "$tgz.part" "$tgz"
    fi
    if curl -fsSL -o "$tgz.sha256" "$URL.sha256" 2>/dev/null && [[ -s "$tgz.sha256" ]]; then
      local want got
      want="$(awk '{print $1}' "$tgz.sha256")"
      got="$(sha256sum "$tgz" | awk '{print $1}')"
      if [[ "$want" != "$got" ]]; then echo "sha256 mismatch" >&2; rm -f "$tgz"; exit 1; fi
      echo "sha256 verified"
    else
      echo "warning: no published .sha256 found; checksum not verified" >&2
    fi
    tar -xzf "$tgz" -C "$BASE"
  fi
  configure
  set_password
}

# The managed block is rewritten on every install/start so ports and memory stay in sync.
configure() {
  local conf="$HOME_DIR/conf/neo4j.conf"
  sed -i '/^# >>> de-jev managed/,/^# <<< de-jev managed/d' "$conf"
  cat >> "$conf" <<CONF
# >>> de-jev managed
server.default_listen_address=127.0.0.1
server.http.listen_address=127.0.0.1:$HTTP_PORT
server.bolt.listen_address=127.0.0.1:$BOLT_PORT
server.memory.heap.initial_size=512m
server.memory.heap.max_size=512m
server.memory.pagecache.size=256m
dbms.usage_report.enabled=false
dbms.security.auth_minimum_password_length=4
# <<< de-jev managed
CONF
}

set_password() {
  # Only before the system database exists; afterwards the stored credentials are kept.
  [[ -d "$HOME_DIR/data/databases/system" ]] && return 0
  local pw=""
  [[ -f "$ENV_FILE" ]] && pw="$(grep -E '^NEO4J_PASSWORD=' "$ENV_FILE" | tail -n1 | cut -d= -f2- | tr -d '\r"' || true)"
  if [[ ${#pw} -lt 4 ]]; then echo "NEO4J_PASSWORD missing or shorter than 4 characters in $ENV_FILE" >&2; exit 1; fi
  "$HOME_DIR/bin/neo4j-admin" dbms set-initial-password "$pw" >/dev/null
  echo "initial password set"
}

start() {
  if is_up; then echo "already running on :$BOLT_PORT"; return 0; fi
  [[ -x "$HOME_DIR/bin/neo4j" ]] || install
  configure
  set_password
  "$HOME_DIR/bin/neo4j" start >"$BASE/start.log" 2>&1 || { cat "$BASE/start.log" >&2; exit 1; }
  local i
  for ((i = 0; i < START_TIMEOUT; i++)); do
    if is_up; then echo "bolt ready on 127.0.0.1:$BOLT_PORT"; return 0; fi
    sleep 1
  done
  echo "timeout waiting for bolt; see $HOME_DIR/logs/neo4j.log" >&2
  exit 1
}

stop() {
  if [[ -x "$HOME_DIR/bin/neo4j" ]]; then "$HOME_DIR/bin/neo4j" stop >/dev/null 2>&1 || true; fi
  echo "stopped"
}

status() {
  if is_up; then echo "running on 127.0.0.1:$BOLT_PORT"; else echo "stopped"; exit 3; fi
}

case "${1:-}" in
  install) install ;;
  start) start ;;
  stop) stop ;;
  status) status ;;
  *) echo "usage: $0 {install|start|stop|status}" >&2; exit 2 ;;
esac
