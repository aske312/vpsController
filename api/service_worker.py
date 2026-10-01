"""Execute a managed service action outside the lifetime of its HTTP request."""
from __future__ import annotations

import os
import json
from pathlib import Path
import sys

import application_operation as operations


def execute(api, service_id: str, action: str, identity: str, unit: str) -> bool:
    return execute_operation(api, f"service-action:{service_id}:{action}",
                             lambda: api.perform_service_action(service_id, api.ServiceAction(action=action)), identity, unit)


def execute_operation(api, name: str, perform, identity: str, unit: str) -> bool:
    identity = operations.operation_id(identity)
    with operations.mutation_lock(api.DATA_DIR):
        stored = operations.read(api.DATA_DIR / "operations" / f"{identity}.json")
        if stored.get("action") != name or stored.get("unit") != unit:
            raise operations.OperationConflict("Service command does not match admission")
        if stored.get("state") in operations.TERMINAL:
            return stored["state"] == "succeeded"
        started = stored["started_at"]
        operations.write_status(api.DATA_DIR, api.ACTION_FILE, name, "running", 10,
                                "Проверка условий выполнения", started, identity, unit)
        try:
            # Recheck ownership and the recovery path at execution time.
            perform()
        except Exception:
            api.logger.exception("Managed service operation failed: %s", identity)
            operations.write_status(api.DATA_DIR, api.ACTION_FILE, name, "failed", 10,
                                    "Операция не завершена успешно; проверьте диагностику и состояние перед повтором",
                                    started, identity, unit)
            return False
        operations.write_status(api.DATA_DIR, api.ACTION_FILE, name, "succeeded", 100,
                                "Результат операции подтверждён", started, identity, unit)
        return True


def apply_network_request(api, name: str, path: Path, digest: str, identity: str):
    payload = operations.load_payload(api.DATA_DIR, identity, path, digest)
    if name != "dns-recover":
        api.check_network_revision(name, payload)
    if name == "dns-settings":
        settings = api.DnsSettingsUpdate.model_validate(payload)
        api.require_dns_management(settings)
        from dns_transaction import configure
        configure(api, settings)
    elif name == "dns-recover":
        from dns_transaction import recover
        recover(api)
    elif name == "network-settings":
        api.update_network_endpoints(api.NetworkEndpointSettings.model_validate(payload))
    elif name == "network-delete":
        if payload["kind"] not in {"cdn", "tls_relay", "udp_relay"}:
            raise ValueError("Invalid route kind")
        api.delete_network_endpoint(payload["kind"], payload["domain"])
    else:
        raise ValueError("Unknown network operation")


if __name__ == "__main__":
    import main as api

    identity = os.environ["VPS_CONTROL_OPERATION_ID"]
    unit = os.environ["VPS_CONTROL_OPERATION_UNIT"]
    if sys.argv[1] == "--network":
        name, path, digest = sys.argv[2:5]
        success = execute_operation(api, name, lambda: apply_network_request(api, name, Path(path), digest, identity), identity, unit)
    elif sys.argv[1] == "--automation-recover":
        from automation_transaction import recover

        success = execute_operation(api, "automation-recover", lambda: recover(api.AUTOMATION_FILE), identity, unit)
    elif sys.argv[1] == "--automation":
        from automation_transaction import configure

        settings = api.AutomationSettings.model_validate(json.loads(sys.argv[2])).model_dump()
        script = api.INSTALL_DIR / "scripts" / "vps-control.sh"
        command = ["/bin/bash", str(script)] if script.exists() else [api.CONTROL_COMMAND]
        success = execute_operation(api, "automation-config",
            lambda: configure(settings, api.AUTOMATION_FILE, command), identity, unit)
    else:
        success = execute(api, sys.argv[1], sys.argv[2], identity, unit)
    sys.exit(0 if success else 1)
