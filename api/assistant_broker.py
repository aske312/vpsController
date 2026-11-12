"""Bounded, fair admission for one API process; never replay model operations."""
from contextlib import contextmanager
from dataclasses import dataclass
import threading
import time
import uuid

from fastapi import HTTPException


@dataclass
class Ticket:
    identity: str
    actor: str
    provider: str
    created: float
    state: str = 'queued'
    weight: int = 0


class Broker:
    def __init__(self, capacity=16, per_actor=3, parallel=2, wait_seconds=135):
        self.capacity, self.per_actor, self.parallel, self.wait_seconds = capacity, per_actor, parallel, wait_seconds
        self.condition = threading.Condition()
        self.waiting = []
        self.running = {}
        self.recent = {}
        self.last_actor = {}
        self.cooldowns = {}

    def _eligible(self):
        providers = {ticket.provider for ticket in self.running.values()}
        actors = {ticket.actor for ticket in self.running.values()}
        candidates = [ticket for ticket in self.waiting if ticket.provider not in providers and ticket.actor not in actors and self.cooldowns.get(ticket.provider, 0) <= time.monotonic()]
        if not candidates or len(self.running) >= self.parallel: return None
        first = candidates[0]
        # Alternate users when possible; a burst from one user cannot monopolize
        # the same provider while another user is already waiting.
        same_provider = [ticket for ticket in candidates if ticket.provider == first.provider]
        return next((ticket for ticket in same_provider if ticket.actor != self.last_actor.get(first.provider)), first)

    @contextmanager
    def admit(self, actor, provider, identity=None, weight=0):
        identity = identity or uuid.uuid4().hex
        with self.condition:
            now = time.monotonic()
            self.recent = {key: value for key, value in self.recent.items() if now - value[1] < 900}
            tickets = self.waiting + list(self.running.values())
            if identity in self.recent or any(ticket.identity == identity for ticket in tickets):
                raise HTTPException(409, 'Запрос уже принят. Проверьте его состояние и историю диалога; автоматический повтор запрещён.')
            if len(tickets) >= self.capacity or sum(ticket.actor == actor for ticket in tickets) >= self.per_actor or sum(ticket.weight for ticket in tickets) + weight > 32 * 1024 * 1024:
                raise HTTPException(429, 'Очередь AI заполнена или достигнут лимит пользователя. Дождитесь ответа.', headers={'Retry-After': '5'})
            ticket = Ticket(identity, actor, provider, now, weight=weight)
            self.waiting.append(ticket)
            try:
                while self._eligible() is not ticket:
                    remaining = self.wait_seconds - (time.monotonic() - now)
                    if remaining <= 0:
                        raise HTTPException(503, 'Время ожидания AI в очереди истекло. Запрос не запускался.', headers={'Retry-After': '5'})
                    self.condition.wait(min(remaining, .5))
                self.waiting.remove(ticket)
                ticket.state = 'running'
                self.running[identity] = ticket
                self.last_actor[provider] = actor
            except BaseException:
                if ticket in self.waiting: self.waiting.remove(ticket)
                self.recent[identity] = (actor, time.monotonic(), 'not_started')
                self.condition.notify_all()
                raise
        try:
            yield ticket
        finally:
            with self.condition:
                self.running.pop(identity, None)
                self.recent[identity] = (actor, time.monotonic(), 'finished')
                # Retain identifiers only; never keep prompts, keys or answers.
                if len(self.recent) > 1000: self.recent.pop(next(iter(self.recent)))
                self.condition.notify_all()

    def status(self, actor, identity=None):
        with self.condition:
            tickets = self.waiting + list(self.running.values())
            own = next((ticket for ticket in tickets if ticket.actor == actor and ticket.identity == identity), None)
            state = own.state if own else 'unknown'
            if not own and identity in self.recent and self.recent[identity][0] == actor: state = self.recent[identity][2]
            return {'state': state, 'position': self.waiting.index(own) + 1 if own in self.waiting else 0,
                    'waiting': len(self.waiting), 'running': len(self.running), 'capacity': self.capacity,
                    'own_waiting': sum(ticket.actor == actor for ticket in self.waiting),
                    'own_running': sum(ticket.actor == actor for ticket in self.running.values())}

    def defer(self, provider, seconds):
        with self.condition:
            self.cooldowns[provider] = max(self.cooldowns.get(provider, 0), time.monotonic() + seconds)
            self.condition.notify_all()


broker = Broker()
