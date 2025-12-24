import inspect
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from types import NoneType, UnionType
from typing import Any, Self, Union, get_args, get_origin, get_type_hints, overload

type InjectionPlan = dict[str, type[Any]]


logger = logging.getLogger(__name__)


class Lifecycle(Enum):
    SINGLETON = auto()
    TRANSIENT = auto()


class ContainerError(Exception):
    """Base class for container errors."""

    pass


class DependencyNotFoundError(ContainerError):
    """Raised when a dependency cannot be resolved."""

    pass


class CircularDependencyError(ContainerError):
    """Raised when a circular dependency is detected."""

    pass


class RegistrationError(ContainerError):
    """Raised when there is an issue with service registration."""

    pass


@dataclass(slots=True, frozen=True, kw_only=True)
class ServiceRegistration[T]:
    """Immutable record of a registered service."""

    lifecycle: Lifecycle
    implementation: type[T] | None = None
    factory: Callable[[Any], T] | None = None


def _get_type_name(type_hint: Any) -> str:
    """Returns a human-readable type name."""
    if type_hint is None or type_hint is type(None):
        return "None"
    if isinstance(type_hint, type):
        return type_hint.__name__
    # Handle Optional/Union types nicely
    origin = get_origin(type_hint)
    args = get_args(type_hint)

    if origin in (Union, UnionType):
        return " | ".join(_get_type_name(arg) for arg in args)

    return str(type_hint).replace("typing.", "")


class Container:
    """A lightweight Dependency Injection container.

    Supports:
    - Singleton and Transient life-cycles.
    - Type-safe resolution.
    - Factory-based registration.
    - Circular dependency detection.
    """

    __slots__ = ("_injection_plans", "_instances", "_lock", "_registry")

    def __init__(self) -> None:
        """Initialize the dependency injection container."""
        self._registry: dict[type[Any], ServiceRegistration[Any]] = {}
        self._instances: dict[type[Any], Any] = {}
        self._lock = threading.RLock()
        self._injection_plans: dict[type[Any], InjectionPlan] = {}

    @overload
    def register[T](
        self,
        interface: type[T],
        *,
        lifecycle: Lifecycle = Lifecycle.SINGLETON,
    ) -> None: ...
    @overload
    def register[T](
        self,
        interface: type[T],
        implementation: type[T],
        *,
        lifecycle: Lifecycle = Lifecycle.SINGLETON,
    ) -> None: ...
    @overload
    def register[T](
        self,
        interface: type[T],
        *,
        factory: Callable[[Self], T],
        lifecycle: Lifecycle = Lifecycle.SINGLETON,
    ) -> None: ...
    def register[T](
        self,
        interface: type[T],
        implementation: type[T] | None = None,
        factory: Callable[[Self], T] | None = None,
        lifecycle: Lifecycle = Lifecycle.SINGLETON,
    ) -> None:
        """Register a dependency.

        Supports three registration patterns:
        1. Self-binding: `register(ConcreteService)`
        2. Implementation binding: `register(IService, ConcreteService)`
        3. Factory binding: `register(IService, factory=lambda c: ConcreteService())`

        Args:
            interface: The abstract base class or interface type
            implementation: The concrete class implementation
            factory: A callable that accepts the container and returns an instance
            lifecycle: The lifecycle strategy (SINGLETON or TRANSIENT)

        Raises:
            RegistrationError: If both implementation and factory are provided

        """
        if implementation and factory:
            raise RegistrationError("Cannot provide both implementation and factory.")

        if implementation is None and factory is None:
            implementation = interface

        if implementation and not inspect.isclass(implementation):
            raise RegistrationError(
                f"Implementation must be a class, got {type(implementation)}"
            )
        with self._lock:
            self._registry[interface] = ServiceRegistration(
                lifecycle=lifecycle,
                implementation=implementation,
                factory=factory,
            )

    def resolve[T](self, interface: type[T]) -> T:
        """Resolves the dependency with thread safety and cycle detection."""
        return self._resolve_impl(interface, set())

    def _resolve_impl[T](
        self, interface: type[T], resolution_stack: set[type[Any]]
    ) -> T:
        is_optional, actual_interface = self._unwrap_optional(interface)
        if actual_interface in resolution_stack:
            stack = " -> ".join(_get_type_name(t) for t in resolution_stack)
            interface_type = _get_type_name(actual_interface)
            raise CircularDependencyError(
                f"Circular dependency detected: {stack} -> {interface_type}"
            )

        if actual_interface in self._instances:
            return self._instances[actual_interface]

        with self._lock:
            if actual_interface in self._instances:
                return self._instances[actual_interface]

            if actual_interface not in self._registry:
                if is_optional:
                    return None  # pyright: ignore[reportReturnType]
                interface_type = _get_type_name(actual_interface)
                logger.debug("Dependency not found: %s out of %s", interface_type, self)
                raise DependencyNotFoundError(
                    f"Service - {interface_type} not registered."
                )

            registration = self._registry[actual_interface]

            # Prepare stack for recursion
            resolution_stack.add(actual_interface)
            try:
                instance = self._create_instance(registration, resolution_stack)
            finally:
                resolution_stack.remove(actual_interface)

            if registration.lifecycle is Lifecycle.SINGLETON:
                self._instances[actual_interface] = instance

            return instance

    def _unwrap_optional(self, interface: type[Any]) -> tuple[bool, type[Any]]:
        """Extract the actual type from Optional[T] or T | None.

        Multi-type unions like `A | B | None` are NOT unwrapped
        and will require explicit registration.

        Returns:
        (is_optional, actual_type)

        """
        origin = get_origin(interface)
        if origin not in (Union, UnionType):
            return False, interface

        args = get_args(interface)
        if NoneType not in args:
            return False, interface

        non_none_args = [arg for arg in args if arg is not NoneType]
        if len(non_none_args) == 1:
            return True, non_none_args[0]

        return False, interface

    def _create_instance(
        self, registration: ServiceRegistration[Any], stack: set[type[Any]]
    ) -> Any:
        if registration.factory:
            return registration.factory(self)

        if registration.implementation:
            return self._inject_dependencies(registration.implementation, stack)

        interface_name = next(iter(stack)) if stack else "Unknown"
        raise RegistrationError(
            f"Invalid registration state for {_get_type_name(interface_name)}"
        )

    def _inject_dependencies[T](
        self, implementation: type[T], stack: set[type[Any]]
    ) -> T:
        if implementation not in self._injection_plans:
            self._analyze_dependencies(implementation)

        signature = inspect.signature(implementation.__init__)
        dependencies = {}
        plan = self._injection_plans[implementation]

        for param_name, param_type in plan.items():
            try:
                dependencies[param_name] = self._resolve_impl(param_type, stack)
            except DependencyNotFoundError:
                param = signature.parameters[param_name]
                if param.default is not inspect.Parameter.empty:
                    dependencies[param_name] = param.default
                else:
                    raise

        return implementation(**dependencies)

    def _analyze_dependencies(self, implementation: type[Any]) -> None:
        """Introspects __init__ once and caches the type hints."""
        try:
            type_hints = get_type_hints(implementation.__init__)
        except Exception:
            type_hints = {}

        signature = inspect.signature(implementation.__init__)
        plan: InjectionPlan = {}

        for name, param in signature.parameters.items():
            if name == "self":
                continue

            dep_type = type_hints.get(name, param.annotation)

            if dep_type is inspect.Parameter.empty:
                continue

            plan[name] = dep_type

        self._injection_plans[implementation] = plan

    def get_registrations(self) -> dict[type[Any], ServiceRegistration[Any]]:
        """Get a snapshot of all registered services for diagnostics.

        Returns:
            A copy of the service registry

        """
        with self._lock:
            return self._registry.copy()

    def is_registered(self, interface: type[Any]) -> bool:
        """Check if a service is registered without resolving it."""
        with self._lock:
            _, actual = self._unwrap_optional(interface)
            return actual in self._registry

    def clear(self) -> None:
        """Clear all registrations and cached instances.

        Useful for testing or resetting the container state.
        """
        with self._lock:
            self._registry.clear()
            self._instances.clear()
            self._injection_plans.clear()

    def close(self) -> None:
        """Shuts down the container and closes all Singletons."""
        with self._lock:
            for instance in reversed(list(self._instances.values())):
                closer = getattr(instance, "close", None) or getattr(
                    instance, "dispose", None
                )
                if callable(closer):
                    try:
                        closer()
                    except Exception as e:
                        print(f"Error closing {type(instance).__name__}: {e}")

            self._instances.clear()

    def __enter__(self) -> Self:
        """Context manager entry point. Returns the container instance."""
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        """Context manager exit point. Shuts down the container."""
        self.close()

    def service[T](
        self,
        interface: type[T] | None = None,
        lifecycle: Lifecycle = Lifecycle.SINGLETON,
    ) -> Callable[[type[T]], type[T]]:
        """Decorator to register a class immediately."""

        def decorator(cls: type[T]) -> type[T]:
            # Use the class itself as the interface if none provided
            register_interface = interface or cls
            self.register(register_interface, implementation=cls, lifecycle=lifecycle)
            return cls

        return decorator

    def __repr__(self) -> str:
        """Returns a string representation of the container.

        Includes the number of registered services and their names.

        Example:
            Container(services=5, registered=['Service1', 'Service2', ...])

        """
        with self._lock:
            service_names = [_get_type_name(t) for t in self._registry.keys()]
            return (
                f"Container(services={len(service_names)}, registered={service_names})"
            )
