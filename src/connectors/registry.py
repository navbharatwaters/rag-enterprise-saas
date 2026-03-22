"""Connector registry — tracks and instantiates connector types."""

from __future__ import annotations

import logging
from typing import Type

from src.connectors.base import BaseConnector

logger = logging.getLogger(__name__)

# Global registry mapping connector_type → class
_REGISTRY: dict[str, Type[BaseConnector]] = {}


def register_connector(cls: Type[BaseConnector]) -> Type[BaseConnector]:
    """Class decorator that registers a connector type.

    Usage::

        @register_connector
        class MyConnector(BaseConnector):
            connector_type = "my_source"
            ...
    """
    if not cls.connector_type:
        raise ValueError(f"{cls.__name__} must set connector_type")
    _REGISTRY[cls.connector_type] = cls
    logger.debug("Registered connector type: %s", cls.connector_type)
    return cls


def get_connector_class(connector_type: str) -> Type[BaseConnector]:
    """Get the connector class for *connector_type*.

    Raises ``KeyError`` if the type is not registered.
    """
    if connector_type not in _REGISTRY:
        raise KeyError(
            f"Unknown connector type: '{connector_type}'. "
            f"Available: {', '.join(sorted(_REGISTRY))}"
        )
    return _REGISTRY[connector_type]


def get_connector_instance(
    connector_type: str,
    config: dict,
    credentials: dict,
) -> BaseConnector:
    """Create and return a connector instance."""
    cls = get_connector_class(connector_type)
    return cls(config=config, credentials=credentials)


def list_connector_types() -> list[str]:
    """Return sorted list of registered connector type names."""
    return sorted(_REGISTRY.keys())


def _clear_registry() -> None:
    """Clear the registry. For testing only."""
    _REGISTRY.clear()
