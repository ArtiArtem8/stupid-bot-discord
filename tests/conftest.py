import asyncio
from collections.abc import Generator

import pytest

from di.container import Container
from events.event_bus import EventBus


@pytest.fixture
def container() -> Container:
    """Provide a fresh DI container for each test."""
    return Container()


@pytest.fixture
def event_bus() -> EventBus:
    """Provide a fresh EventBus for each test."""
    return EventBus()


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create an instance of the default event loop for the session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()
