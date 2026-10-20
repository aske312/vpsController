#!/usr/bin/env bash
# Shared by Direct and Mihomo installers. A release supplies one reviewed core.
stage_xray_core() {
  local destination="$1" root bundle
  root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
  bundle="${root}/api/bin/xray"
  [[ -x "${bundle}" && -r "${bundle}.sha256" ]] || {
    echo 'В релизе отсутствует подготовленное ядро Xray. Установите готовую сборку vpsController.' >&2
    return 1
  }
  (cd -- "${root}/api/bin" && sha256sum -c xray.sha256) || return 1
  "${bundle}" version | grep -F 'vpsController-trojan-fragment-v1' >/dev/null || {
    echo 'В релизе отсутствует исправление фрагментированного заголовка Trojan.' >&2
    return 1
  }
  install -m 0755 -- "${bundle}" "${destination}"
}
