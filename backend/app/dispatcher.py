from __future__ import annotations

from collections.abc import Callable

from .domain import DomainEvent

EventHandler = Callable[[DomainEvent], None]


class DeterministicEventDispatcher:
    """Synchronous in-process dispatcher preserving subscription and call order."""

    def __init__(self) -> None:
        self._handlers: list[EventHandler] = []

    def subscribe(self, handler: EventHandler) -> Callable[[], None]:
        self._handlers.append(handler)

        def unsubscribe() -> None:
            self._handlers.remove(handler)

        return unsubscribe

    def dispatch(self, event: DomainEvent) -> None:
        for handler in tuple(self._handlers):
            handler(event)
