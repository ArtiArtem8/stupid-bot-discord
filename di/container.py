from __future__ import annotations

import inspect
import logging
import threading
from _thread import RLock as RLockType
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from types import NoneType, TracebackType, UnionType
from typing import (
    Protocol,
    Self,
    cast,
    get_args,
    get_origin,
    get_type_hints,
    overload,
    override,
    runtime_checkable,
)

type ServiceKey = type[object]
type InjectionPlan = dict[str, object]

logger = logging.getLogger(__name__)


class Lifecycle(Enum):
    SINGLETON = auto()
    TRANSIENT = auto()


class ContainerError(Exception):
    """Base class for container errors."""


class DependencyNotFoundError(ContainerError):
    """Raised when a dependency cannot be resolved."""


class CircularDependencyError(ContainerError):
    """Raised when a circular dependency is detected."""


class RegistrationError(ContainerError):
    """Raised when a service registration is invalid."""


@dataclass(slots=True, frozen=True, kw_only=True)
class ServiceRegistration:
    """Immutable record of a registered service."""

    lifecycle: Lifecycle
    implementation: ServiceKey | None = None
    factory: Callable[[Container], object] | None = None


@runtime_checkable
class SupportsClose(Protocol):
    def close(self) -> None: ...


@runtime_checkable
class SupportsDispose(Protocol):
    def dispose(self) -> None: ...


def _get_type_name(type_hint: object) -> str:
    """Return a human-readable type name for diagnostics."""
    if type_hint is None or type_hint is NoneType:
        return "None"
    if isinstance(type_hint, type):
        return type_hint.__name__

    origin = cast(object, get_origin(type_hint))
    args = cast(tuple[object, ...], get_args(type_hint))

    if origin is UnionType:
        return " | ".join(_get_type_name(arg) for arg in args)

    return str(type_hint).replace("typing.", "")


def _unwrap_optional_type(type_hint: object) -> tuple[bool, ServiceKey | None]:
    """Return (is_optional, service_key) for T or T | None annotations."""
    if isinstance(type_hint, type):
        return False, type_hint

    origin = cast(object, get_origin(type_hint))
    if origin is not UnionType:
        return False, None

    args = cast(tuple[object, ...], get_args(type_hint))
    if NoneType not in args:
        return False, None

    non_none_args = [arg for arg in args if arg is not NoneType]
    if len(non_none_args) != 1:
        return False, None

    inner = non_none_args[0]
    if not isinstance(inner, type):
        return False, None

    return True, inner


def _get_init_type_hints(implementation: ServiceKey) -> dict[str, object]:
    try:
        return cast(dict[str, object], get_type_hints(implementation.__init__))
    except Exception:
        return {}


class Container:
    """A lightweight dependency injection container.

    Supports singleton/transient lifecycles, factory registrations,
    constructor injection, and circular dependency detection.
    """

    __slots__: tuple[str, ...] = (
        "_injection_plans",
        "_instances",
        "_lock",
        "_registry",
    )

    def __init__(self) -> None:
        self._registry: dict[ServiceKey, ServiceRegistration] = {}
        self._instances: dict[ServiceKey, object] = {}
        self._lock: RLockType = threading.RLock()
        self._injection_plans: dict[ServiceKey, InjectionPlan] = {}

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
        implementation: ServiceKey,
        *,
        lifecycle: Lifecycle = Lifecycle.SINGLETON,
    ) -> None: ...

    @overload
    def register[T](
        self,
        interface: type[T],
        *,
        factory: Callable[[Container], T],
        lifecycle: Lifecycle = Lifecycle.SINGLETON,
    ) -> None: ...

    def register[T](
        self,
        interface: type[T],
        implementation: object | None = None,
        factory: Callable[[Container], T] | None = None,
        lifecycle: Lifecycle = Lifecycle.SINGLETON,
    ) -> None:
        """Register a dependency.

        Supported patterns:
        - ``register(ConcreteService)``
        - ``register(IService, ConcreteService)``
        - ``register(IService, factory=lambda c: ConcreteService())``
        """
        if implementation is not None and factory is not None:
            raise RegistrationError("Cannot provide both implementation and factory.")

        implementation_type: ServiceKey | None
        if implementation is None and factory is None:
            implementation_type = interface
        elif implementation is None:
            implementation_type = None
        elif inspect.isclass(implementation):
            implementation_type = implementation
        else:
            raise RegistrationError(
                f"Implementation must be a class, got {type(implementation)}"
            )

        stored_factory = cast(Callable[[Container], object] | None, factory)
        with self._lock:
            self._registry[interface] = ServiceRegistration(
                lifecycle=lifecycle,
                implementation=implementation_type,
                factory=stored_factory,
            )

    def resolve[T](self, interface: type[T]) -> T:
        """Resolve a required dependency."""
        return cast(T, self._resolve_required_key(interface, set()))

    def resolve_optional[T](self, interface: type[T]) -> T | None:
        """Resolve a dependency, returning None when it is not registered."""
        try:
            return self.resolve(interface)
        except DependencyNotFoundError:
            return None

    def _resolve_required_key(
        self,
        interface: ServiceKey,
        resolution_stack: set[ServiceKey],
    ) -> object:
        if interface in resolution_stack:
            stack = " -> ".join(_get_type_name(t) for t in resolution_stack)
            raise CircularDependencyError(
                f"Circular dependency detected: {stack} -> {_get_type_name(interface)}"
            )

        if interface in self._instances:
            return self._instances[interface]

        with self._lock:
            if interface in self._instances:
                return self._instances[interface]

            registration = self._registry.get(interface)
            if registration is None:
                logger.debug(
                    "Dependency not found: %s out of %s",
                    _get_type_name(interface),
                    self,
                )
                raise DependencyNotFoundError(
                    f"Service - {_get_type_name(interface)} not registered."
                )

            resolution_stack.add(interface)
            try:
                instance = self._create_instance(registration, resolution_stack)
            finally:
                resolution_stack.remove(interface)

            if registration.lifecycle is Lifecycle.SINGLETON:
                self._instances[interface] = instance

            return instance

    def _resolve_dependency(
        self,
        type_hint: object,
        resolution_stack: set[ServiceKey],
    ) -> object:
        is_optional, service_key = _unwrap_optional_type(type_hint)
        if service_key is None:
            raise DependencyNotFoundError(
                f"Cannot resolve non-class dependency: {_get_type_name(type_hint)}"
            )

        try:
            return self._resolve_required_key(service_key, resolution_stack)
        except DependencyNotFoundError:
            if is_optional:
                return None
            raise

    def _create_instance(
        self,
        registration: ServiceRegistration,
        stack: set[ServiceKey],
    ) -> object:
        if registration.factory is not None:
            return registration.factory(self)

        if registration.implementation is not None:
            return self._inject_dependencies(registration.implementation, stack)

        interface_name = next(iter(stack), None)
        raise RegistrationError(
            f"Invalid registration state for {_get_type_name(interface_name)}"
        )

    def _inject_dependencies[T](
        self,
        implementation: type[T],
        stack: set[ServiceKey],
    ) -> T:
        if implementation not in self._injection_plans:
            self._analyze_dependencies(implementation)

        signature = inspect.signature(implementation.__init__)
        dependencies: dict[str, object] = {}
        plan = self._injection_plans[implementation]

        for param_name, param_type in plan.items():
            try:
                dependencies[param_name] = self._resolve_dependency(param_type, stack)
            except DependencyNotFoundError:
                param = signature.parameters[param_name]
                default_value = cast(object, param.default)
                if default_value is not inspect.Parameter.empty:
                    dependencies[param_name] = default_value
                else:
                    raise

        return implementation(**dependencies)

    def _analyze_dependencies(self, implementation: ServiceKey) -> None:
        """Introspect __init__ once and cache dependency annotations."""
        type_hints = _get_init_type_hints(implementation)
        signature = inspect.signature(implementation.__init__)
        plan: InjectionPlan = {}

        for name, param in signature.parameters.items():
            if name == "self":
                continue

            dep_type = type_hints.get(name)
            if dep_type is None:
                annotation = cast(object, param.annotation)
                if annotation is inspect.Parameter.empty:
                    continue
                dep_type = annotation

            plan[name] = dep_type

        self._injection_plans[implementation] = plan

    def get_registrations(self) -> dict[ServiceKey, ServiceRegistration]:
        """Return a snapshot of all registered services for diagnostics."""
        with self._lock:
            return self._registry.copy()

    def is_registered(self, interface: object) -> bool:
        """Return whether a service key or optional service key is registered."""
        _, service_key = _unwrap_optional_type(interface)
        if service_key is None:
            return False

        with self._lock:
            return service_key in self._registry

    def clear(self) -> None:
        """Clear all registrations, cached instances, and injection plans."""
        with self._lock:
            self._registry.clear()
            self._instances.clear()
            self._injection_plans.clear()

    def close(self) -> None:
        """Close all singleton instances that expose `close()` or `dispose()`."""
        with self._lock:
            for instance in reversed(list(self._instances.values())):
                try:
                    if isinstance(instance, SupportsClose):
                        instance.close()
                    elif isinstance(instance, SupportsDispose):
                        instance.dispose()
                except Exception as e:
                    logger.warning(
                        "Error closing %s: %s",
                        type(instance).__name__,
                        e,
                        exc_info=True,
                    )

            self._instances.clear()

    def __enter__(self) -> Self:
        """Enter the runtime context and return this container."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Exit the runtime context by closing the container."""
        self.close()

    def service[T](
        self,
        interface: type[T] | None = None,
        lifecycle: Lifecycle = Lifecycle.SINGLETON,
    ) -> Callable[[type[T]], type[T]]:
        """Decorator that registers a class immediately."""

        def decorator(cls: type[T]) -> type[T]:
            register_interface = interface or cls
            self.register(register_interface, implementation=cls, lifecycle=lifecycle)
            return cls

        return decorator

    @override
    def __repr__(self) -> str:
        with self._lock:
            service_names = [_get_type_name(t) for t in self._registry.keys()]
            return (
                f"Container(services={len(service_names)}, registered={service_names})"
            )
