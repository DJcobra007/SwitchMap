"""Tiny in-memory store so downloads can refer to the analysis a user just ran."""
from __future__ import annotations
import threading
from collections import OrderedDict

_MAX = 30
_lock = threading.Lock()
_store: "OrderedDict[str, object]" = OrderedDict()


def put(analysis) -> str:
    with _lock:
        _store[analysis.id] = analysis
        while len(_store) > _MAX:
            _store.popitem(last=False)
    return analysis.id


def get(sid: str):
    with _lock:
        return _store.get(sid)
