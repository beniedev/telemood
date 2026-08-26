"""Read-only static checks for a Telemood host adapter."""

from __future__ import annotations

from dataclasses import dataclass
import inspect
from typing import Any


@dataclass(frozen=True)
class MethodCheck:
    name: str
    present: bool
    callable: bool
    synchronous: bool
    signature_compatible: bool
    detail: str | None = None

    @property
    def passed(self) -> bool:
        return (
            self.present
            and self.callable
            and self.synchronous
            and self.signature_compatible
        )


@dataclass(frozen=True)
class AdapterCheckResult:
    """Static conformance result; it never asserts live delivery."""

    methods: tuple[MethodCheck, ...]
    static_only: bool = True
    live_delivery_verified: bool = False

    @property
    def passed(self) -> bool:
        return self.static_only and not self.live_delivery_verified and all(
            method.passed for method in self.methods
        )

    @property
    def issues(self) -> tuple[str, ...]:
        return tuple(
            f"{method.name}: {method.detail or 'not compatible'}"
            for method in self.methods
            if not method.passed
        )

    @property
    def ok(self) -> bool:
        return self.passed


_METHOD_ARITIES = {
    "send_bubble": 2,
    "send_reaction": 2,
    "send_choices": 3,
    "send_sticker_sequence": 3,
}


def check_adapter(adapter: object) -> AdapterCheckResult:
    """Check the four synchronous ``InteractionHost`` methods statically.

    ``inspect.getattr_static`` is used so descriptors are inspected without
    invoking adapter methods or any transport.  The check only validates
    callable shape; it cannot prove Telegram delivery.
    """

    checks = tuple(
        _check_method(adapter, name, arity)
        for name, arity in _METHOD_ARITIES.items()
    )
    return AdapterCheckResult(methods=checks)


def _check_method(adapter: object, name: str, arity: int) -> MethodCheck:
    try:
        raw = inspect.getattr_static(adapter, name)
    except (AttributeError, TypeError):
        return MethodCheck(
            name=name,
            present=False,
            callable=False,
            synchronous=False,
            signature_compatible=False,
            detail="missing",
        )

    function, signature = _bound_callable(raw)
    if function is None or signature is None:
        return MethodCheck(
            name=name,
            present=True,
            callable=False,
            synchronous=False,
            signature_compatible=False,
            detail="not callable",
        )

    unwrapped = inspect.unwrap(function)
    synchronous = not inspect.iscoroutinefunction(unwrapped)
    if not synchronous:
        return MethodCheck(
            name=name,
            present=True,
            callable=True,
            synchronous=False,
            signature_compatible=False,
            detail="async method is not allowed",
        )

    try:
        signature.bind(*([object()] * arity))
    except TypeError as exc:
        return MethodCheck(
            name=name,
            present=True,
            callable=True,
            synchronous=True,
            signature_compatible=False,
            detail=f"incompatible signature: {exc}",
        )
    return MethodCheck(
        name=name,
        present=True,
        callable=True,
        synchronous=True,
        signature_compatible=True,
    )


def _bound_callable(raw: object) -> tuple[Any | None, inspect.Signature | None]:
    """Return a static callable and its signature after binding ``self``."""

    drop_first = False
    if isinstance(raw, staticmethod):
        function = raw.__func__
    elif isinstance(raw, classmethod):
        function = raw.__func__
        drop_first = True
    elif inspect.isfunction(raw):
        function = raw
        drop_first = True
    elif inspect.ismethod(raw):
        function = raw
    elif callable(raw):
        function = raw
    else:
        return None, None

    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):
        return None, None
    if drop_first:
        parameters = tuple(signature.parameters.values())
        if not parameters:
            return None, None
        signature = signature.replace(parameters=parameters[1:])
    return function, signature
