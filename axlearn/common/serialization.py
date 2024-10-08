# Copyright © 2024 Apple Inc.
#
# The code in this file is adapted from:
#
# google/flax:
# Copyright 2024 The Flax Authors.
# Licensed under the Apache License, Version 2.0 (the "License").

"""Adapted from flax.serialization with minor changes."""

import threading
from contextlib import contextmanager
from typing import Any, Callable

import jax

_STATE_DICT_REGISTRY: dict[Any, Any] = {}


class _ErrorContext(threading.local):
    """Context for deserialization error messages."""

    def __init__(self):
        super().__init__()
        self.path = []


_error_context = _ErrorContext()


@contextmanager
def _record_path(name):
    try:
        _error_context.path.append(name)
        yield
    finally:
        _error_context.path.pop()


def current_path():
    """Current state_dict path during deserialization for error messages."""
    return "/".join(_error_context.path)


class _NamedTuple:
    """Fake type marker for namedtuple for registry."""


def _is_namedtuple(x: Any) -> bool:
    """Duck typing test for namedtuple factory-generated objects."""
    return isinstance(x, tuple) and hasattr(x, "_fields")


def to_state_dict(target: Any) -> dict[str, Any]:
    """Returns a dictionary with the state of the given target.

    Equivalent to `flax.serialization.to_state_dict`.

    Args:
        target: The target instance to produce a state dict for.

    Returns:
        The state dict for the target.
    """
    if _is_namedtuple(target):
        ty = _NamedTuple
    else:
        ty = type(target)
    if ty not in _STATE_DICT_REGISTRY:
        return target

    ty_to_state_dict = _STATE_DICT_REGISTRY[ty][0]
    state_dict = ty_to_state_dict(target)
    if isinstance(state_dict, dict):
        for key in state_dict.keys():
            if not isinstance(key, str):
                raise ValueError(
                    "A state dict must only have string keys. "
                    f"Instead, encountered key {key} of type {type(key)}."
                )
    return state_dict


def from_state_dict(target: Any, state: dict[str, Any], name: str = ".") -> Any:
    """Restores the state of the given target using a state dict.

    Equivalent to `flax.serialization.from_state_dict`.

    Args:
        target: The object of which the state should be restored.
        state: A dictionary generated by `to_state_dict` with the desired new state for `target`.
        name: Name of branch taken, used to improve deserialization error messages.

    Returns:
        A copy of the object with the restored state.
    """
    if _is_namedtuple(target):
        ty = _NamedTuple
    else:
        ty = type(target)
    if ty not in _STATE_DICT_REGISTRY:
        return state
    ty_from_state_dict = _STATE_DICT_REGISTRY[ty][1]
    with _record_path(name):
        return ty_from_state_dict(target, state)


def register_serialization_state(
    ty: type, ty_to_state_dict: Callable, ty_from_state_dict: Callable, override: bool = False
):
    """Register a type for serialization.

    Equivalent to `flax.serialization.from_state_dict`.

    Args:
        ty: The type to be registered.
        ty_to_state_dict: A function that takes an instance of `ty` and returns its state as a
            dictionary.
        ty_from_state_dict: A function that takes an instance of `ty` and a state dict, and returns
            a copy of the instance with the restored state.
        override: Whether to override a previously registered serialization handler.
    """
    if ty in _STATE_DICT_REGISTRY and not override:
        raise ValueError(f'A serialization handler for "{ty.__name__}" is already registered.')
    _STATE_DICT_REGISTRY[ty] = (ty_to_state_dict, ty_from_state_dict)


# Below are serialization implementations for standard container types.


def _list_state_dict(xs: list[Any]) -> dict[str, Any]:
    return {str(i): to_state_dict(x) for i, x in enumerate(xs)}


def _restore_list(xs: list[Any], state_dict: dict[str, Any]) -> list[Any]:
    if len(state_dict) != len(xs):
        raise ValueError(
            "The size of the list and the state dict do not match, "
            f"got {len(xs)} and {len(state_dict)} at path {current_path()}"
        )
    return [from_state_dict(xs[i], state_dict[str(i)], name=str(i)) for i in range(len(xs))]


def _dict_state_dict(xs: dict[str, Any]) -> dict[str, Any]:
    str_keys = {str(k) for k in xs.keys()}
    if len(str_keys) != len(xs):
        raise ValueError(
            "Dict keys do not have a unique string representation: " f"{str_keys} vs given: {xs}"
        )
    return {str(key): to_state_dict(value) for key, value in xs.items()}


def _restore_dict(xs: dict[str, Any], state_dict: dict[str, Any]) -> dict[str, Any]:
    diff = {str(k) for k in xs.keys()}.difference(state_dict.keys())
    if diff:
        raise ValueError(
            "The target dict keys and state dict keys do not match, target dict "
            f"contains keys {diff} which are not present in state dict at path "
            f"{current_path()}"
        )
    return {
        key: from_state_dict(value, state_dict[str(key)], name=str(key))
        for key, value in xs.items()
    }


def _namedtuple_state_dict(xs) -> dict[str, Any]:
    return {key: to_state_dict(getattr(xs, key)) for key in xs._fields}


def _restore_namedtuple(xs, state_dict: dict[str, Any]):
    state_keys = set(state_dict.keys())
    namedtuple_keys = set(xs._fields)
    if state_keys != namedtuple_keys:
        raise ValueError(
            "The field names of the state dict and the named tuple do not match, "
            f"got {state_keys} and {namedtuple_keys} at path {current_path()}"
        )
    fields = {k: from_state_dict(getattr(xs, k), v, name=k) for k, v in state_dict.items()}
    return type(xs)(**fields)


register_serialization_state(dict, _dict_state_dict, _restore_dict)
register_serialization_state(list, _list_state_dict, _restore_list)
register_serialization_state(
    tuple,
    _list_state_dict,
    lambda xs, state_dict: tuple(_restore_list(list(xs), state_dict)),
)
register_serialization_state(_NamedTuple, _namedtuple_state_dict, _restore_namedtuple)
register_serialization_state(
    jax.tree_util.Partial,
    lambda x: ({"args": to_state_dict(x.args), "keywords": to_state_dict(x.keywords)}),
    lambda x, sd: jax.tree_util.Partial(
        x.func,
        *from_state_dict(x.args, sd["args"]),
        **from_state_dict(x.keywords, sd["keywords"]),
    ),
)
