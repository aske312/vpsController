"""Admission guard from PRO e0fe5f6, before application recovery journals."""
class OperationConflict(ValueError):
    pass


def require_recovery_clear(data_dir, action=None):
    if action in {"mihomo-module-recover:profiles", "dns-recover", "automation-recover", "network-check", "integrity-check", "doctor", "ssh-access-rollback", "ssh-access-disable"}:
        return
    pending = [name for name in ("dns-recovery.json", "automation-recovery.json", "mihomo/profile-recovery/manifest.json", "recovery/security-recovery.txt") if (data_dir / name).exists()]
    if pending:
        raise OperationConflict("Сначала восстановите прерванные настройки DNS/расписаний/Mihomo/защиты; новые изменения заблокированы")
