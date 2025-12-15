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

        logger.info(f"Running diagnostics on {len(registrations)} services...")

        for interface, reg in registrations.items():
            # We mostly care about validating Singletons at startup to fail fast
            if reg.lifecycle is Lifecycle.SINGLETON:
                try:
                    # Dry run resolution
                    # Note: This will instantiate them!
                    # For production, we might want a 'dry_run=True' mode in resolve
                    # that just checks graph connectivity without instantiation,
                    # but instantiation is the ultimate test.
                    self._container.resolve(interface)
                except Exception as e:
                    error_msg = f"Failed to resolve {interface.__name__}: {e!s}"
                    errors.append(error_msg)
                    logger.error(error_msg)

        return errors

    def print_graph(self) -> None:
        """Print the dependency graph (simplified)."""
        # This would require inspecting implementation signatures recursively
        pass
