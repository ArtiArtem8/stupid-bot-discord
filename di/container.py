import inspect
from collections.abc import Callable
from enum import Enum, auto
from typing import Any, get_type_hints


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


class Container:
    """A lightweight Dependency Injection container.

    Supports:
    - Singleton and Transient lifecycles.
    - Type-safe resolution.
    - Factory-based registration.
    - Circular dependency detection.
    """

    def __init__(self) -> None:
        self._registry: dict[type[Any], dict[str, Any]] = {}
        self._instances: dict[type[Any], Any] = {}
        self._resolving: set[type[Any]] = set()

    def register[T](
        self,
        interface: type[T],
        implementation: type[T] | None = None,
        factory: Callable[["Container"], T] | None = None,
        lifecycle: Lifecycle = Lifecycle.SINGLETON,
    ) -> None:
        """Register a dependency.

        Args:
            interface: The abstract base class or interface type.
            implementation: The concrete class implementation.
            factory: A generic callable that accepts the container and returns an instance.
            lifecycle: The lifecycle strategy (SINGLETON or TRANSIENT).

        """
        if implementation and factory:
            raise RegistrationError("Cannot provide both implementation and factory.")

        if not implementation and not factory:
            # Self-binding
            implementation = interface

        self._registry[interface] = {
            "implementation": implementation,
            "factory": factory,
            "lifecycle": lifecycle,
        }

    def resolve[T](self, interface: type[T]) -> T:
        """Resolve a dependency.

        Args:
            interface: The type to resolve.

        Returns:
            An instance of type T.

        Raises:
            DependencyNotFoundError: If the type is not registered.
            CircularDependencyError: If a cycle is detected in dependencies.

        """
        # Check if interface is actually a type, not a string
        if isinstance(interface, str):
            raise RegistrationError(
                f"String '{interface}' passed as interface type. Expected a type/class."
            )

        if interface in self._resolving:
            raise CircularDependencyError(
                f"Circular dependency detected for {interface.__name__}"
            )

        if interface not in self._registry:
            raise DependencyNotFoundError(
                f"Service {interface.__name__} not registered."
            )

        registration = self._registry[interface]
        lifecycle = registration["lifecycle"]

        # Return existing singleton if available
        if lifecycle == Lifecycle.SINGLETON and interface in self._instances:
            return self._instances[interface]

        self._resolving.add(interface)
        try:
            instance = self._create_instance(registration)
        finally:
            self._resolving.remove(interface)

        if lifecycle == Lifecycle.SINGLETON:
            self._instances[interface] = instance

        return instance

    def _create_instance(self, registration: dict[str, Any]) -> Any:
        factory = registration.get("factory")
        if factory:
            return factory(self)

        implementation = registration["implementation"]
        if not implementation:
            raise RegistrationError("No implementation or factory provider found.")

        # Auto-wiring for classes
        return self._inject_dependencies(implementation)

    def _inject_dependencies[T](self, implementation: type[T]) -> T:
        """Instantiate a class by resolving its type-hinted __init__ dependencies."""
        if not inspect.isclass(implementation):
            # It might be an instance if strictly registered, but usually we deal with types
            raise RegistrationError(
                f"Implementation must be a class, got {type(implementation)}"
            )

        # Get type hints with proper resolution of forward references
        try:
            type_hints = get_type_hints(implementation.__init__)
        except (NameError, AttributeError):
            # If get_type_hints fails, fall back to manual inspection
            init_signature = inspect.signature(implementation.__init__)
            params = init_signature.parameters
            type_hints = {}
            for param_name, param in params.items():
                if param_name != "self" and param.annotation != inspect.Parameter.empty:
                    type_hints[param_name] = param.annotation

        dependencies = {}
        init_signature = inspect.signature(implementation.__init__)
        for param_name, param in init_signature.parameters.items():
            if param_name == "self":
                continue

            if param.annotation == inspect.Parameter.empty:
                # Skip un-annotated parameters or provide None?
                # Best practice: strict DI requires types.
                continue

            # Get the resolved type from get_type_hints, fallback to raw annotation
            dependency_type = type_hints.get(param_name, param.annotation)
            # Check if dependency_type is actually a type/class, not a string
            if isinstance(dependency_type, str):
                raise RegistrationError(
                    f"String annotation '{dependency_type}' found for parameter '{param_name}'. "
                    f"This usually means postponed evaluation of annotations is enabled and "
                    f"string annotations aren't being resolved properly."
                )
            try:
                dependencies[param_name] = self.resolve(dependency_type)
            except DependencyNotFoundError:
                if param.default != inspect.Parameter.empty:
                    dependencies[param_name] = param.default
                else:
                    raise

        return implementation(**dependencies)

    def get_registrations(self) -> dict[type[Any], dict[str, Any]]:
        """Get a copy of the registry for diagnostics purposes."""
        return self._registry.copy()
