"""Shared pytest fixtures."""

import asyncio
from collections.abc import Generator

import pytest

from di.container import Container
from events.event_bus import EventBus


@pytest.fixture
def container() -> Container:
    return Container()


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
