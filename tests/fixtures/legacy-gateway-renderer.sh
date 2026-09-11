# Renderer from the installed release before gateway policy placeholders.
write_caddy_config() {
  local domain internal_panel_host wg_panel_address awg_panel_address
  domain="$(env_value PUBLIC_DOMAIN)"
  internal_panel_host="admin.312.net"
  wg_panel_address="$(python3 - "$(env_value WG_SUBNET)" <<'PY'
import ipaddress
import sys
print(next(ipaddress.ip_network(sys.argv[1] or "10.72.0.0/24").hosts()))
PY
)"
  awg_panel_address="$(python3 - "$(env_value AWG_SUBNET)" <<'PY'
import ipaddress
import sys
print(next(ipaddress.ip_network(sys.argv[1] or "10.73.0.0/24").hosts()))
PY
)"
  install -d -m 0755 "${CADDY_SNIPPET_DIR}"
  if [[ -n "${domain}" && "${ACCESS_MODE}" == "external" ]]; then
    sed -e "s|{\$SITE_ADDRESS}|${domain}|g" -e "s|{\$HTTP_PORT}|${HTTP_PORT}|g" \
      -e "s|{WG_PANEL_ADDRESS}|${wg_panel_address}|g" -e "s|{AWG_PANEL_ADDRESS}|${awg_panel_address}|g" \
      -e "s|{INTERNAL_PANEL_HOST}|${internal_panel_host}|g" -e "s|{PUBLIC_PANEL_ADDRESS}|$(env_value PUBLIC_IP_ENDPOINT)|g" \
      "${INSTALL_DIR}/Caddyfile" >"${CADDY_CONFIG}"
  elif [[ "${ACCESS_MODE}" == "vpn" ]]; then
    # Keep TCP 80/443 available to protocol-specific Caddy hosts (for example
    # VLESS CDN), but never attach the panel to a public catch-all listener.
    sed -e "s|{\$SITE_ADDRESS}|http://localhost:${HTTP_PORT}|g" -e "s|{\$HTTP_PORT}|${HTTP_PORT}|g" \
      -e "s|{WG_PANEL_ADDRESS}|${wg_panel_address}|g" -e "s|{AWG_PANEL_ADDRESS}|${awg_panel_address}|g" \
      -e "s|{INTERNAL_PANEL_HOST}|${internal_panel_host}|g" -e "s|{PUBLIC_PANEL_ADDRESS}|$(env_value PUBLIC_IP_ENDPOINT)|g" \
      "${INSTALL_DIR}/Caddyfile" >"${CADDY_CONFIG}"
  else
    sed -e "s|{\$SITE_ADDRESS}|:${HTTP_PORT}|g" -e "s|{\$HTTP_PORT}|${HTTP_PORT}|g" \
      -e "s|{WG_PANEL_ADDRESS}|${wg_panel_address}|g" -e "s|{AWG_PANEL_ADDRESS}|${awg_panel_address}|g" \
      -e "s|{INTERNAL_PANEL_HOST}|${internal_panel_host}|g" -e "s|{PUBLIC_PANEL_ADDRESS}|$(env_value PUBLIC_IP_ENDPOINT)|g" \
      "${INSTALL_DIR}/Caddyfile" >"${CADDY_CONFIG}"
  fi
  if [[ "${ACCESS_MODE}" == "vpn" && -n "${domain}" ]]; then
    cat >>"${CADDY_CONFIG}" <<EOF

# Public, token-protected Mihomo subscription refresh. No panel UI or
# administrative API is exposed on this host while VPN-only mode is active.
${domain} {
    header {
        -Server
        -X-Powered-By
    }
    handle /s/* {
        reverse_proxy 127.0.0.1:8791
    }
    handle /api/mihomo/subscriptions/* {
        reverse_proxy 127.0.0.1:8791
    }
    respond 404
}
EOF
  fi
}
