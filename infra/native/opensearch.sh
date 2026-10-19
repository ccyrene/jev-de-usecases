#!/usr/bin/env bash
# Single-node OpenSearch 3.9.0 (linux-x64 tarball, bundled JDK), security plugin disabled,
# bound to 127.0.0.1. Usage: opensearch.sh {install|start|stop|status}
set -euo pipefail

VERSION="3.9.0"
BASE="${DEBENCH_CACHE:-$HOME/.cache/de-jev}/opensearch"
HOME_DIR="$BASE/opensearch-$VERSION"
DATA_DIR="$BASE/data"
LOG_DIR="$BASE/logs"
PID_FILE="$BASE/opensearch.pid"
PORT="${OPENSEARCH_PORT:-9200}"
URL="https://artifacts.opensearch.org/releases/bundle/opensearch/$VERSION/opensearch-$VERSION-linux-x64.tar.gz"
START_TIMEOUT="${OPENSEARCH_START_TIMEOUT:-240}"

health() { curl -fsS "http://127.0.0.1:$PORT/_cluster/health?wait_for_status=yellow&timeout=2s" 2>/dev/null; }

is_up() { curl -fsS -o /dev/null "http://127.0.0.1:$PORT/" 2>/dev/null; }

install() {
  if [[ -x "$HOME_DIR/bin/opensearch" ]]; then echo "already installed: $HOME_DIR"; else
    mkdir -p "$BASE"
    local tgz="$BASE/opensearch-$VERSION.tar.gz"
    if [[ ! -s "$tgz" ]]; then
      curl -fL --retry 3 -C - -o "$tgz.part" "$URL"
      mv "$tgz.part" "$tgz"
    fi
    if curl -fsSL -o "$tgz.sha512" "$URL.sha512" 2>/dev/null; then
      local want got
      want="$(awk '{print $1}' "$tgz.sha512")"
      got="$(sha512sum "$tgz" | awk '{print $1}')"
      if [[ "$want" != "$got" ]]; then echo "sha512 mismatch" >&2; rm -f "$tgz"; exit 1; fi
      echo "sha512 verified"
    else
      echo "warning: no published .sha512 found; checksum not verified" >&2
    fi
    tar -xzf "$tgz" -C "$BASE"
  fi
  mkdir -p "$DATA_DIR" "$LOG_DIR"
  cat > "$HOME_DIR/config/opensearch.yml" <<YML
cluster.name: de-jev
node.name: de-jev-1
discovery.type: single-node
network.host: 127.0.0.1
http.port: $PORT
path.data: $DATA_DIR
path.logs: $LOG_DIR
plugins.security.disabled: true
action.auto_create_index: false
# the process may sit in a cgroup the security manager cannot read, so percentages cannot resolve
indices.memory.native_index_buffer_size: 64mb
YML
}

start() {
  if is_up; then echo "already running on :$PORT"; return 0; fi
  [[ -x "$HOME_DIR/bin/opensearch" ]] || install
  mkdir -p "$DATA_DIR" "$LOG_DIR"
  export OPENSEARCH_JAVA_OPTS="${OPENSEARCH_JAVA_OPTS:--Xms512m -Xmx512m -XX:-UseContainerSupport}"
  export DISABLE_INSTALL_DEMO_CONFIG=true
  export DISABLE_SECURITY_PLUGIN=true
  setsid nohup "$HOME_DIR/bin/opensearch" -p "$PID_FILE" > "$LOG_DIR/stdout.log" 2>&1 < /dev/null &
  local i
  for ((i = 0; i < START_TIMEOUT; i++)); do
    if health >/dev/null; then echo "ready on 127.0.0.1:$PORT"; return 0; fi
    sleep 1
  done
  echo "timeout waiting for yellow cluster; see $LOG_DIR/stdout.log" >&2
  exit 1
}

stop() {
  if [[ -s "$PID_FILE" ]]; then
    local pid; pid="$(cat "$PID_FILE")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid"
      for _ in $(seq 1 60); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
  fi
  echo "stopped"
}

status() {
  if is_up; then echo "running"; curl -fsS "http://127.0.0.1:$PORT/" | grep -m1 '"number"' || true; else echo "stopped"; exit 3; fi
}

case "${1:-}" in
  install) install ;;
  start) start ;;
  stop) stop ;;
  status) status ;;
  *) echo "usage: $0 {install|start|stop|status}" >&2; exit 2 ;;
esac
