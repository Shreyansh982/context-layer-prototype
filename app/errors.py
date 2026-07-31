"""Errors that can safely cross the HTTP boundary."""


class DependencyUnavailable(RuntimeError):
    """A required external dependency could not complete an operation."""
