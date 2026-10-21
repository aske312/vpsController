"""Controlled UDP path with correlated observations at client, Relay and origin."""
import json
import secrets
import socket
import threading
import time

from relay_test_cleanup import atomic, firewall_rule, cleanup
from relay_test_runner import measurement_sequence
import subprocess
import math
import shutil
import ipaddress
from relay_client import AgentAPIError


def sequences(value):
    if not isinstance(value, list) or len(value) > 100 or any(type(item) is not int or not 0 <= item < 100 for item in value) or len(value) != len(set(value)):
        raise ValueError('Invalid measurement sequences')
    return set(value)


def analyse(client, agent, origin):
    if client.get('state') != 'completed': return {'state': 'unknown', 'reason': 'measurement_unavailable'}
    sent, received = sequences(client['sent_sequences']), sequences(client['received_sequences'])
    ingress, forwarded = sequences(agent['received_sequences']), sequences(agent['forwarded_sequences'])
    returned, egress = sequences(agent['returned_sequences']), sequences(agent['sent_sequences'])
    at_origin, from_origin = sequences(origin['received_sequences']), sequences(origin['sent_sequences'])
    if not received <= egress <= returned <= from_origin <= at_origin <= forwarded <= ingress <= sent:
        return {'state': 'unknown', 'reason': 'measurement_inconsistent'}
    if not sent: return {'state': 'unknown', 'reason': 'measurement_unavailable'}
    legs = [len(sent - ingress), len(forwarded - at_origin), len(from_origin - returned), len(egress - received)]
    reason = 'client_to_relay_no_packets' if not ingress else 'relay_to_vps_no_reply' if not returned else 'relay_to_client_no_reply' if not received else 'packet_loss' if received != sent else 'udp_verified'
    def rtt(value):
        if value is None: return None
        if type(value) not in (float, int) or not math.isfinite(value) or not 0 <= value <= 120000: raise ValueError('Invalid RTT')
        return value
    return {'state': 'ready' if received == sent else 'failed' if not received else 'warning', 'reason': reason,
            'sent': len(sent), 'received': len(received), 'loss_percent': round(100 * len(sent - received) / len(sent), 2),
            'client_to_relay_lost': legs[0], 'relay_to_vps_lost': legs[1], 'vps_to_relay_lost': legs[2], 'relay_to_client_lost': legs[3],
            'agent_local_drops': agent['local_drops'], 'agent_socket_errors': agent['socket_errors'],
            'origin_local_drops': len(at_origin - from_origin), 'rtt_ms': rtt(client.get('rtt_ms')), 'relay_vps_rtt_ms': rtt(agent.get('upstream_rtt_ms')),
            'packet_size': 1200, 'scope': 'diagnostic_udp_path'}


class Measurement:
    def __init__(self, api, route, host, directory, ttl=600):
        self.api, self.route, self.host, self.directory, self.ttl = api, route, host, directory, ttl
        self.id = directory.name
        self.key = secrets.token_hex(32)
        self.client = api.relay_control().client(host)
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.received, self.sent = set(), set()
        self.socket = self.thread = None
        self.remote_created = False
        self.cleanup_pending = False

    def __enter__(self):
        try:
            if ipaddress.ip_address(self.client.host).version != 4 or not shutil.which('iptables'):
                raise OSError('Diagnostic listener prerequisites unavailable')
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.bind(('0.0.0.0', 0)); self.socket.settimeout(0.1)
            port = self.socket.getsockname()[1]
            journal = self.directory/'cleanup.json'
            record = json.loads(journal.read_text())
            record['firewall'] = {'id': self.id, 'source': self.client.host, 'port': port}
            record['measurement'] = {'id': self.id, 'host_id': self.host['id'], 'route_id': self.route['id']}
            atomic(journal, record)
            subprocess.run(['iptables', '-w', '5', '-I', *firewall_rule(record['firewall'])], check=True, capture_output=True, timeout=8)
            self.thread = threading.Thread(target=self.serve, daemon=True); self.thread.start()
            # Known ephemeral ID, bounded lifetime. A lost PUT is never replayed.
            self.remote_created = True
            data = self.client.request('PUT', '/v1/measurements/' + self.id,
                {'route_id': self.route['id'], 'target_ip': self.api.PUBLIC_IPV4, 'target_port': port, 'key': self.key, 'ttl_seconds': self.ttl})
            if data.get('id') != self.id or data.get('route_id') != self.route['id'] or type(data.get('listen_port')) is not int or not 20000 <= data['listen_port'] <= 20999:
                raise ValueError('Invalid Agent measurement endpoint')
            self.port = data['listen_port']
            return self
        except BaseException:
            self.close(); raise

    def serve(self):
        while not self.stop.is_set():
            try:
                data, peer = self.socket.recvfrom(2048)
                if peer[0] != self.client.host: continue
                number = measurement_sequence(data, self.id, self.key)
                if number is None: continue
                with self.lock:
                    if number in self.received: continue
                    self.received.add(number)
                    self.socket.sendto(data, peer)
                    self.sent.add(number)
            except socket.timeout: continue
            except OSError: break

    def settings(self):
        return {'id': self.id, 'key': self.key, 'address': self.client.host, 'port': self.port}

    def result(self, client_result):
        # Never trust an arbitrary remote error string or a client-provided loss percentage.
        agent = self.client.request('GET', '/v1/measurements/' + self.id)
        if agent.get('id') != self.id or agent.get('route_id') != self.route['id']: raise ValueError('Wrong measurement')
        with self.lock:
            origin = {'received_sequences': sorted(self.received), 'sent_sequences': sorted(self.sent)}
        for key in ('local_drops', 'socket_errors'):
            if type(agent.get(key)) is not int or not 0 <= agent[key] <= 1000: raise ValueError('Invalid measurement counters')
        return analyse(client_result, agent, origin)

    def close(self):
        self.stop.set()
        if self.socket: self.socket.close()
        if self.thread: self.thread.join(timeout=1)
        if self.remote_created:
            try:
                observed = self.client.request('GET', '/v1/measurements/' + self.id)
                if observed.get('id') == self.id and observed.get('route_id') == self.route['id']:
                    if observed.get('state') != 'expired': self.client.request('DELETE', '/v1/measurements/' + self.id)
                else: self.cleanup_pending = True
            except AgentAPIError as exc:
                self.cleanup_pending = exc.status != 404
            except Exception: self.cleanup_pending = True  # Agent TTL still removes the listener.
        journal = self.directory/'cleanup.json'
        if journal.exists():
            record = json.loads(journal.read_text())
            if record.get('measurement'):
                record['remote_cleaned'] = not self.cleanup_pending
                atomic(journal, record)
        cleanup(self.directory/'cleanup.json')

    def __exit__(self, *_): self.close()
