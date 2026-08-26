#!/usr/bin/env bash
set -Eeuo pipefail
INTERFACE="mh-awg0"
CONFIG="/etc/amnezia/amneziawg/${INTERFACE}.conf"
PORT="$(sed -n 's/^ListenPort[[:space:]]*=[[:space:]]*//p' "${CONFIG}" 2>/dev/null | head -n1 || true)"
systemctl disable --now "awg-quick@${INTERFACE}.service" >/dev/null 2>&1 || true
[[ -z "${PORT}" ]] || { command -v ufw >/dev/null && ufw delete allow "${PORT}/udp" >/dev/null 2>&1 || true; }
rm -f -- "${CONFIG}" "/etc/amnezia/${INTERFACE}.conf" /etc/sysctl.d/99-vps-control-mihomo-awg.conf
if ! find /etc/amnezia/amneziawg -mindepth 1 -maxdepth 1 -name '*.conf' -print -quit 2>/dev/null | grep -q .; then
  apt-get -o DPkg::Lock::Timeout=300 purge -y amneziawg amneziawg-tools amneziawg-dkms
  add-apt-repository --remove -y ppa:amnezia/ppa >/dev/null 2>&1 || true
  rm -f -- /etc/apt/sources.list.d/amnezia-ppa.list /usr/share/keyrings/amnezia-ppa.gpg
fi
systemctl daemon-reload
echo "Mihomo/AmneziaWG удалён; direct awg0 не изменялся."
