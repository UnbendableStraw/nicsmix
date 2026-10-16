#!/bin/sh
# Deploy the agent to running cells over SSH (no reflash needed).
#   ./deploy.sh agent/syng-agent.py 192.168.1.10 192.168.1.11 …      or set SYNG_CELLS="ip ip …"
# Keeps /usr/bin/syng-agent.prev.bak on each cell for rollback. Needs keys/syng_ed25519 (see README) or an ssh-agent.
set -e
SRC="$1"; shift || true
[ -f "$SRC" ] || { echo "usage: $0 syng-agent-bNN.py [ip ...]"; exit 1; }
CELLS="${*:-${SYNG_CELLS:-}}"
[ -n "$CELLS" ] || { echo "no cells given: pass IPs or set SYNG_CELLS"; exit 1; }
KEY="${SYNG_KEY:-$(dirname "$0")/../keys/syng_ed25519}"
SSHO="$( [ -f "$KEY" ] && echo "-i $KEY" ) -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8 -o LogLevel=ERROR"
echo "local md5: $(md5 -q "$SRC" 2>/dev/null || md5sum "$SRC" | cut -d' ' -f1)"
for ip in $CELLS; do
  echo "== $ip"
  ssh $SSHO "root@$ip" 'cat > /usr/bin/syng-agent.new' < "$SRC"
  ssh $SSHO "root@$ip" 'cp -f /usr/bin/syng-agent /usr/bin/syng-agent.prev.bak; mv -f /usr/bin/syng-agent.new /usr/bin/syng-agent; chmod 755 /usr/bin/syng-agent; md5sum /usr/bin/syng-agent | cut -d" " -f1; systemctl restart syng-agent; sleep 2; systemctl is-active syng-agent'
done
echo "rollback on a cell: ssh -i keys/syng_ed25519 root@IP 'cp -f /usr/bin/syng-agent.prev.bak /usr/bin/syng-agent && systemctl restart syng-agent'"
