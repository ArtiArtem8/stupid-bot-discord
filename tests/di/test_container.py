"""Tests for dependency injection container behavior.
Covers registration, resolution, lifecycles, and concurrency safety.
"""

from __future__ import annotations

import threading
import time
import unittest
from collections.abc import Callable
from typing import Protocol, cast, override

from di.container import (
    CircularDependencyError,
    Container,
    DependencyNotFoundError,
    Lifecycle,
    RegistrationError,
)


class IRepository(Protocol):
    def get_data(self) -> str: ...


class MemoryRepository:
    def __init__(self) -> None:
        self.call_count: int = 0

    def get_data(self) -> str:
        self.call_count += 1
        return "data"


class IService(Protocol):
    def execute(self) -> str: ...


class ConcreteService:
    def __init__(self, repo: IRepository) -> None:
        self.repo: IRepository = repo

    def execute(self) -> str:
        return self.repo.get_data()


class OptionalDependencyService:
    def __init__(self, repo: IRepository | None = None) -> None:
        self.repo: IRepository | None = repo

    def has_repo(self) -> bool:
        return self.repo is not None


class DefaultValueService:
    def __init__(self, value: str = "default") -> None:
        self.value: str = value


class ComplexService:
    def __init__(
        self,
        service: IService,
        repo: IRepository,
        name: str = "complex",
    ) -> None:
        self.service: IService = service
        self.repo: IRepository = repo
        self.name: str = name


class ServiceA:
    def __init__(self, b: ServiceB) -> None:
        self.b: ServiceB = b


class ServiceB:
    def __init__(self, a: ServiceA) -> None:
        self.a: ServiceA = a


class SimpleService:
    instance_count: int = 0

    def __init__(self) -> None:
        SimpleService.instance_count += 1
        self.id: int = SimpleService.instance_count


class ValueService:
    value: int = -1


def make_value_service(index: int) -> type[ValueService]:
    """Create a value service class with a fixed integer value.

    Args:
        index: The integer value to assign to the generated service.

    Returns:
        A dynamically created subclass of ValueService with the value set.

    """

    class DynamicValueService(ValueService):
        def __init__(self) -> None:
            self.value: int = index

    DynamicValueService.__name__ = f"Service{index}"
    return DynamicValueService


class InvalidRegisterCall(Protocol):
    def __call__(
        self,
        interface: object,
        implementation: type[object],
        *,
        factory: Callable[[Container], IRepository],
    ) -> None: ...


class ContainerTestCase(unittest.TestCase):
    def __init__(self, methodName: str = "runTest") -> None:
        super().__init__(methodName)
        self.container: Container = Container()

    @override
    def setUp(self) -> None:
        self.container = Container()


class TestContainerRegistration(ContainerTestCase):
    def test_self_binding_registration(self) -> None:
        self.container.register(MemoryRepository)
        self.assertTrue(self.container.is_registered(MemoryRepository))

    def test_interface_implementation_binding(self) -> None:
        self.container.register(IRepository, MemoryRepository)
        self.assertTrue(self.container.is_registered(IRepository))

    def test_factory_registration(self) -> None:
        self.container.register(
            IRepository,
            factory=lambda _container: MemoryRepository(),
        )
        repo = self.container.resolve(IRepository)
        self.assertIsInstance(repo, MemoryRepository)

    def test_registration_with_both_implementation_and_factory_raises_error(
        self,
    ) -> None:
        invalid_register = cast(InvalidRegisterCall, self.container.register)

        with self.assertRaises(RegistrationError) as ctx:
            invalid_register(
                IRepository,
                MemoryRepository,
                factory=lambda _container: MemoryRepository(),
            )

        self.assertIn("Cannot provide both", str(ctx.exception))

    def test_registration_with_non_class_implementation_raises_error(self) -> None:
        invalid_implementation = cast(type[object], "not_a_class")

        with self.assertRaises(RegistrationError) as ctx:
            self.container.register(IRepository, invalid_implementation)

        self.assertIn("must be a class", str(ctx.exception))

    def test_transient_lifecycle_registration(self) -> None:
        self.container.register(
            MemoryRepository,
            lifecycle=Lifecycle.TRANSIENT,
        )
        repo1 = self.container.resolve(MemoryRepository)
        repo2 = self.container.resolve(MemoryRepository)
        self.assertIsNot(repo1, repo2)

    def test_singleton_lifecycle_registration(self) -> None:
        self.container.register(
            MemoryRepository,
            lifecycle=Lifecycle.SINGLETON,
        )
        repo1 = self.container.resolve(MemoryRepository)
        repo2 = self.container.resolve(MemoryRepository)
        self.assertIs(repo1, repo2)


class TestContainerResolution(ContainerTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        SimpleService.instance_count = 0

    def test_resolve_simple_service(self) -> None:
        self.container.register(SimpleService)
        service = self.container.resolve(SimpleService)
        self.assertIsInstance(service, SimpleService)

    def test_resolve_with_dependencies(self) -> None:
        self.container.register(IRepository, MemoryRepository)
        self.container.register(IService, ConcreteService)

        service = self.container.resolve(IService)
        self.assertIsInstance(service, ConcreteService)
        self.assertEqual(service.execute(), "data")

    def test_resolve_unregistered_service_raises_error(self) -> None:
        with self.assertRaises(DependencyNotFoundError) as ctx:
            _ = self.container.resolve(IRepository)
        self.assertIn("not registered", str(ctx.exception))

    def test_resolve_with_factory(self) -> None:
        call_count = 0

        def factory(_: Container) -> MemoryRepository:
            nonlocal call_count
            call_count += 1
            return MemoryRepository()

        self.container.register(IRepository, factory=factory)
        repo = self.container.resolve(IRepository)

        self.assertIsInstance(repo, MemoryRepository)
        self.assertEqual(call_count, 1)

    def test_singleton_returns_same_instance(self) -> None:
        self.container.register(SimpleService, lifecycle=Lifecycle.SINGLETON)
        instance1 = self.container.resolve(SimpleService)
        instance2 = self.container.resolve(SimpleService)

        self.assertIs(instance1, instance2)
        self.assertEqual(SimpleService.instance_count, 1)

    def test_transient_returns_different_instances(self) -> None:
        self.container.register(SimpleService, lifecycle=Lifecycle.TRANSIENT)
        instance1 = self.container.resolve(SimpleService)
        instance2 = self.container.resolve(SimpleService)

        self.assertIsNot(instance1, instance2)
        self.assertEqual(SimpleService.instance_count, 2)

    def test_resolve_complex_dependency_graph(self) -> None:
        self.container.register(IRepository, MemoryRepository)
        self.container.register(IService, ConcreteService)
        self.container.register(ComplexService)

        service = self.container.resolve(ComplexService)
        self.assertIsInstance(service.service, ConcreteService)
        self.assertIsInstance(service.repo, MemoryRepository)
        self.assertEqual(service.name, "complex")


class TestOptionalDependencies(ContainerTestCase):
    def test_optional_dependency_with_registered_service(self) -> None:
        self.container.register(IRepository, MemoryRepository)
        self.container.register(OptionalDependencyService)

        service = self.container.resolve(OptionalDependencyService)
        self.assertTrue(service.has_repo())

    def test_optional_dependency_without_registered_service(self) -> None:
        self.container.register(OptionalDependencyService)

        service = self.container.resolve(OptionalDependencyService)
        self.assertFalse(service.has_repo())

    def test_resolve_optional_returns_none_for_unregistered_service(self) -> None:
        result = self.container.resolve_optional(IRepository)
        self.assertIsNone(result)

    def test_default_parameter_used_when_dependency_not_found(self) -> None:
        self.container.register(DefaultValueService)
        service = self.container.resolve(DefaultValueService)
        self.assertEqual(service.value, "default")


class TestCircularDependencies(ContainerTestCase):
    def test_circular_dependency_detection(self) -> None:
        self.container.register(ServiceA)
        self.container.register(ServiceB)

        with self.assertRaises(CircularDependencyError) as ctx:
            _ = self.container.resolve(ServiceA)

        error_msg = str(ctx.exception)
        self.assertIn("Circular dependency detected", error_msg)
        self.assertIn("ServiceA", error_msg)
        self.assertIn("ServiceB", error_msg)


class TestThreadSafety(ContainerTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        SimpleService.instance_count = 0

    def test_concurrent_singleton_resolution(self) -> None:
        self.container.register(SimpleService, lifecycle=Lifecycle.SINGLETON)

        instances: list[SimpleService] = []
        lock = threading.Lock()

        def resolve_service() -> None:
            instance = self.container.resolve(SimpleService)
            with lock:
                instances.append(instance)

        threads = [threading.Thread(target=resolve_service) for _ in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(instances), 10)
        self.assertEqual(SimpleService.instance_count, 1)
        for instance in instances:
            self.assertIs(instance, instances[0])

    def test_concurrent_transient_resolution(self) -> None:
        self.container.register(SimpleService, lifecycle=Lifecycle.TRANSIENT)

        instances: list[SimpleService] = []
        lock = threading.Lock()

        def resolve_service() -> None:
            instance = self.container.resolve(SimpleService)
            with lock:
                instances.append(instance)

        threads = [threading.Thread(target=resolve_service) for _ in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(instances), 10)
        self.assertEqual(SimpleService.instance_count, 10)
        unique_instances = {id(instance) for instance in instances}
        self.assertEqual(len(unique_instances), 10)

    def test_concurrent_registration_and_resolution(self) -> None:
        results: list[bool] = []
        lock = threading.Lock()

        def register_and_resolve(index: int) -> None:
            time.sleep(0.001 * index)

            service_type = make_value_service(index)
            self.container.register(service_type)
            instance = self.container.resolve(service_type)

            with lock:
                results.append(instance.value == index)

        threads = [
            threading.Thread(target=register_and_resolve, args=(i,)) for i in range(5)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertTrue(all(results))


class TestContainerUtilities(ContainerTestCase):
    def test_get_registrations(self) -> None:
        self.container.register(MemoryRepository)
        self.container.register(IService, ConcreteService)

        registrations = self.container.get_registrations()
        self.assertEqual(len(registrations), 2)
        self.assertIn(MemoryRepository, registrations)
        self.assertIn(IService, registrations)

    def test_get_registrations_returns_copy(self) -> None:
        self.container.register(MemoryRepository)

        registrations1 = self.container.get_registrations()
        self.container.register(ConcreteService)
        registrations2 = self.container.get_registrations()

        self.assertEqual(len(registrations1), 1)
        self.assertEqual(len(registrations2), 2)

    def test_is_registered_returns_true_for_registered_service(self) -> None:
        self.container.register(MemoryRepository)
        self.assertTrue(self.container.is_registered(MemoryRepository))

    def test_is_registered_returns_false_for_unregistered_service(self) -> None:
        self.assertFalse(self.container.is_registered(MemoryRepository))

    def test_is_registered_handles_optional_types(self) -> None:
        self.container.register(MemoryRepository)
        self.assertTrue(self.container.is_registered(MemoryRepository | None))

    def test_clear_removes_all_registrations_and_instances(self) -> None:
        self.container.register(MemoryRepository, lifecycle=Lifecycle.SINGLETON)
        self.container.register(ConcreteService)

        _ = self.container.resolve(MemoryRepository)
        self.container.clear()

        self.assertEqual(len(self.container.get_registrations()), 0)
        self.assertFalse(self.container.is_registered(MemoryRepository))
        self.assertFalse(self.container.is_registered(ConcreteService))

    def test_repr_shows_registered_services(self) -> None:
        self.container.register(MemoryRepository)
        self.container.register(IService, ConcreteService)

        repr_str = repr(self.container)

        self.assertIn("Container", repr_str)
        self.assertIn("services=2", repr_str)
        self.assertIn("MemoryRepository", repr_str)
        self.assertIn("IService", repr_str)

    def test_repr_empty_container(self) -> None:
        repr_str = repr(self.container)
        self.assertIn("services=0", repr_str)
        self.assertIn("registered=[]", repr_str)

    def test_container_context_manager_cleanup(self) -> None:
        class Database:
            def __init__(self) -> None:
                self.closed: bool = False

            def close(self) -> None:
                self.closed = True

        with Container() as container:
            container.register(Database, lifecycle=Lifecycle.SINGLETON)
            db = container.resolve(Database)
            self.assertFalse(db.closed)

        self.assertTrue(db.closed)


class TestFactoryWithContainerAccess(ContainerTestCase):
    def test_factory_can_resolve_dependencies(self) -> None:
        self.container.register(MemoryRepository)

        def create_service(c: Container) -> ConcreteService:
            repo = c.resolve(MemoryRepository)
            return ConcreteService(repo)

        self.container.register(IService, factory=create_service)
        service = self.container.resolve(IService)

        self.assertIsInstance(service, ConcreteService)
        self.assertEqual(service.execute(), "data")

    def test_factory_respects_lifecycle(self) -> None:
        call_count = 0

        def factory(_: Container) -> SimpleService:
            nonlocal call_count
            call_count += 1
            return SimpleService()

        self.container.register(
            SimpleService,
            factory=factory,
            lifecycle=Lifecycle.SINGLETON,
        )

        instance1 = self.container.resolve(SimpleService)
        instance2 = self.container.resolve(SimpleService)

        self.assertIs(instance1, instance2)
        self.assertEqual(call_count, 1)


class TestEdgeCases(ContainerTestCase):
    def test_resolve_service_with_no_init_parameters(self) -> None:
        class NoInitService:
            pass

        self.container.register(NoInitService)
        service = self.container.resolve(NoInitService)
        self.assertIsInstance(service, NoInitService)

    def test_multiple_optional_dependencies(self) -> None:
        class MultiOptionalService:
            def __init__(
                self,
                repo: IRepository | None = None,
                service: IService | None = None,
            ) -> None:
                self.repo: IRepository | None = repo
                self.service: IService | None = service

        self.container.register(IRepository, MemoryRepository)
        self.container.register(MultiOptionalService)

        service = self.container.resolve(MultiOptionalService)
        self.assertIsNotNone(service.repo)
        self.assertIsNone(service.service)

    def test_registration_lifecycle_persists(self) -> None:
        self.container.register(MemoryRepository, lifecycle=Lifecycle.TRANSIENT)

        registrations = self.container.get_registrations()
        registration = registrations[MemoryRepository]

        self.assertEqual(registration.lifecycle, Lifecycle.TRANSIENT)


if __name__ == "__main__":
    _ = unittest.main()
