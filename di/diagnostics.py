import logging

from .container import Container, Lifecycle

logger = logging.getLogger(__name__)


class Diagnostics:
    """Health check and diagnostic tools for the DI Container."""

    def __init__(self, container: Container) -> None:
        self._container = container

    def check_registrations(self) -> list[str]:
        """Verify that all registered singletons can be resolved.
        Returns a list of error messages, if any.
        """
        errors: list[str] = []
        registrations = self._container.get_registrations()

        logger.info("Running diagnostics on %s services...", len(registrations))

        for interface, reg in registrations.items():
            # We mostly care about validating Singletons at startup to fail fast
            if reg.lifecycle is Lifecycle.SINGLETON:
                try:
                    # ! DANGEROUS!
                    # Dry run resolution
                    # Note: This will instantiate them!
                    self._container.resolve(interface)
                except Exception as exc:
                    error_msg = f"Failed to resolve {interface.__name__}: {exc!s}"
                    errors.append(error_msg)
                    logger.exception("Failed to resolve %s", interface.__name__)

        return errors

    def print_graph(self) -> None:
        """Print the dependency graph (simplified)."""
        pass
