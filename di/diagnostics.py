import logging

from .container import Container, Lifecycle

logger = logging.getLogger(__name__)


class Diagnostics:
    """Health check and diagnostic tools for the DI Container."""

    def __init__(self, container: Container) -> None:
        self._container = container

    def check_registrations(self) -> list[str]:
        """Verify that all registered singletons can be resolved.

        Resolution constructs each singleton and may therefore run its startup
        side effects. Failures are logged and returned as diagnostic messages.
        """
        errors: list[str] = []
        registrations = self._container.get_registrations()

        logger.info("Running diagnostics on %s services...", len(registrations))

        for interface, reg in registrations.items():
            if reg.lifecycle is Lifecycle.SINGLETON:
                try:
                    # Singleton validation intentionally constructs each service.
                    self._container.resolve(interface)
                except Exception as exc:
                    error_msg = f"Failed to resolve {interface.__name__}: {exc!s}"
                    errors.append(error_msg)
                    logger.exception("Failed to resolve %s", interface.__name__)

        return errors

    def print_graph(self) -> None:
        """Print the dependency graph (simplified)."""
