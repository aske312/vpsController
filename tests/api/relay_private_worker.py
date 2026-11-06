"""Isolated Linux integration fixture. Never run against production state."""
import asyncio
import json
from pathlib import Path
import signal
import sys

import agent


async def main():
    folder = Path.cwd()
    profile = json.loads(Path(sys.argv[1]).read_text())
    state = folder/'agent-state'
    config = folder/'agent-config'
    config.mkdir(exist_ok=True)
    (config/'config.json').write_text(json.dumps({'own_ips': [profile['relay_ip']]}))
    instance = agent.Agent(config, state)
    instance.settings = lambda: {'own_ips': [profile['relay_ip']]}
    instance.private_transport.binary = lambda: folder/'sing-box'
    generation = profile['generation']
    private = state/'private-transport'/generation
    if not (private/'request.json').exists():
        instance.private_transport.validate(generation, profile)
        private.mkdir(parents=True)
        agent.atomic_json(private/'request.json', profile)
    else:
        # For the deliberate wrong-key test only, with synthetic credentials.
        agent.atomic_json(private/'request.json', profile)
    await instance.private_transport.restore()
    done = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM): loop.add_signal_handler(sig, done.set)
    await done.wait()
    await instance.private_transport.close()
    assert not instance.private_transport.processes


if __name__ == '__main__':
    asyncio.run(main())
