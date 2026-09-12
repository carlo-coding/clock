"""The record is written once. This is the property everything rests on."""

import pytest

from tick import config, forecast, store
from tick.timeutil import parse_iso


def test_write_new_refuses_to_overwrite(tmp_path):
    p = tmp_path / "x.json"
    store.write_new(p, {"a": 1})
    with pytest.raises(store.AlreadyWritten):
        store.write_new(p, {"a": 2})
    assert store.read(p) == {"a": 1}


def test_append_only_adds_lines(tmp_path):
    p = tmp_path / "x.jsonl"
    store.append_line(p, {"n": 1})
    store.append_line(p, {"n": 2})
    assert store.read_lines(p) == [{"n": 1}, {"n": 2}]


def test_store_module_has_no_update_function():
    assert not any(name.startswith(("update", "rewrite", "overwrite")) for name in dir(store))


def test_issue_is_idempotent_and_never_touches_an_existing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FORECASTS", tmp_path)
    now = parse_iso("2026-09-12T07:40:00Z")
    path = store.forecast_path("2026-09-12")
    store.write_new(path, {"sentinel": True})
    before = path.read_text()
    got, written = forecast.issue(client=None, now=now, history={"DE-LU": {}, "ES": {}})
    assert not written
    assert path.read_text() == before
