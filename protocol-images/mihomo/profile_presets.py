"""Replace only unchanged retired built-ins; preserve user-edited templates."""
import hashlib
import json
from pathlib import Path


RETIRED = json.loads(Path(__file__).with_name("retired-profile-presets.json").read_text(encoding="utf-8"))


def migrate(presets, defaults):
    kept = []
    for preset in presets:
        digest = hashlib.sha256(json.dumps(preset, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        if digest not in RETIRED.get(preset["id"], []):
            kept.append(preset)
    if len(kept) == len(presets):
        return presets
    ids = {row["id"] for row in kept}
    return [*kept, *(row for row in defaults if row["id"] not in ids)][:12]
