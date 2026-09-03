"""Persistence tests. A compile costs money; losing it to a refresh is not ok."""

import json
import os

import pytest

from core import pricing, store
from core.verifier import verify


def a_run(spec=None, document="", **overrides) -> store.SavedRun:
    payload = {
        "title": "Deal desk",
        "document": document,
        "step": 3,
        "claims": spec.claims if spec else None,
        "decisions": spec.decisions if spec else None,
        "spec": spec,
        "report": verify(spec, document) if spec else None,
        "model": "claude-opus-5",
        "input_tokens": 31000,
        "output_tokens": 9000,
        "calls": 4,
    }
    payload.update(overrides)
    return store.SavedRun(**payload)


# --------------------------------------------------------------------------- #
# Round trip
# --------------------------------------------------------------------------- #


def test_a_full_run_round_trips(spec, document):
    store.save(a_run(spec, document))
    loaded = store.load()
    assert loaded is not None
    assert loaded.spec == spec
    assert loaded.claims == spec.claims
    assert loaded.decisions == spec.decisions
    assert loaded.report.passed
    assert loaded.step == 3
    assert loaded.calls == 4


def test_a_run_without_a_spec_round_trips(document):
    store.save(a_run(document=document, step=1))
    loaded = store.load()
    assert loaded is not None and loaded.spec is None
    assert loaded.document == document


def test_saving_stamps_the_time_and_version():
    store.save(a_run(document="x"))
    loaded = store.load()
    assert loaded.version == store.FORMAT_VERSION
    assert loaded.saved_at.startswith("20")


def test_answers_survive_the_round_trip(spec, document):
    spec.decisions[0].answer = "The CFO signs off above 40%."
    spec.decisions[1].answer = None
    store.save(a_run(spec, document))
    loaded = store.load()
    assert loaded.decisions[0].answer == "The CFO signs off above 40%."
    assert loaded.decisions[1].answer is None
    assert not loaded.decisions[1].answered


# --------------------------------------------------------------------------- #
# Absence and corruption are not errors
# --------------------------------------------------------------------------- #


def test_no_saved_run_returns_none():
    assert store.load() is None
    assert not store.exists()


def test_a_corrupt_file_is_treated_as_absent():
    path = store.run_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json at all", encoding="utf-8")
    assert store.load() is None


def test_a_future_format_version_is_ignored():
    store.save(a_run(document="x"))
    path = store.run_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["version"] = store.FORMAT_VERSION + 99
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert store.load() is None


def test_a_file_that_no_longer_validates_is_ignored():
    store.save(a_run(document="x"))
    path = store.run_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["claims"] = [{"id": "NOT-AN-ID"}]
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert store.load() is None


def test_clear_removes_the_run_and_is_safe_to_repeat():
    store.save(a_run(document="x"))
    assert store.exists()
    store.clear()
    assert not store.exists()
    store.clear()  # must not raise


# --------------------------------------------------------------------------- #
# Durability
# --------------------------------------------------------------------------- #


def test_a_failed_write_leaves_the_previous_run_intact(spec, document, monkeypatch):
    """A crash mid-save must not destroy the run that was already there."""
    store.save(a_run(spec, document))
    good = store.run_file().read_text(encoding="utf-8")

    def explode(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(store.os, "replace", explode)
    try:
        store.save(a_run(document="replacement"))
    except OSError:
        pass

    assert store.run_file().read_text(encoding="utf-8") == good
    assert store.load().spec == spec


def test_no_temporary_files_are_left_behind(spec, document):
    store.save(a_run(spec, document))
    leftovers = [f for f in os.listdir(store.store_dir()) if f.endswith(".tmp")]
    assert leftovers == []


def test_the_store_location_is_configurable(monkeypatch, tmp_path):
    monkeypatch.setenv("SPEC_ENGINE_STORE", str(tmp_path / "elsewhere"))
    store.save(a_run(document="x"))
    assert (tmp_path / "elsewhere" / "last_run.json").exists()


# --------------------------------------------------------------------------- #
# The restore prompt
# --------------------------------------------------------------------------- #


def test_describe_summarises_a_compiled_run(spec, document):
    described = a_run(spec, document).describe()
    assert "5 requirements" in described and "7 tasks" in described


def test_describe_summarises_an_earlier_stage(document):
    run = a_run(document=document, step=1)
    run.claims = None
    assert "a document" in run.describe()


def test_a_restored_run_remembers_which_endpoint_produced_it(spec, document):
    """Otherwise a local run reads as an unpriced hosted one after a refresh."""
    from core.verifier import verify

    store.save(
        store.SavedRun(
            document=document,
            spec=spec,
            report=verify(spec, document),
            model="qwen3:32b",
            base_url="http://localhost:11434/v1",
            calls=4,
        )
    )
    restored = store.load()
    assert restored.base_url == "http://localhost:11434/v1"
    assert pricing.actual_cost(
        99_000, 40_000, restored.model, base_url=restored.base_url
    ) == 0


# --------------------------------------------------------------------------- #
# Shared deployments
# --------------------------------------------------------------------------- #


def test_persistence_can_be_turned_off(monkeypatch, spec, document):
    """One process serves every visitor on a hosted deployment.

    Without this switch, the next stranger to open the app would be handed the
    last stranger's document.
    """
    monkeypatch.setenv("SPEC_ENGINE_STORE", "off")
    assert not store.enabled()
    assert store.save(a_run(spec, document)) is None
    assert store.load() is None
    assert store.exists() is False


@pytest.mark.parametrize("value", ["off", "OFF", "none", "no", "0", "disabled", " "])
def test_every_disabling_spelling_is_honoured(monkeypatch, value):
    monkeypatch.setenv("SPEC_ENGINE_STORE", value)
    assert not store.enabled()


def test_persistence_is_on_by_default(monkeypatch, tmp_path, spec, document):
    """A laptop is the normal case, and there a refresh must not lose work."""
    monkeypatch.setenv("SPEC_ENGINE_STORE", str(tmp_path / "store"))
    assert store.enabled()
    assert store.save(a_run(spec, document)) is not None
    assert store.load() is not None


def test_an_explicit_path_still_works_when_disabled(monkeypatch, tmp_path, spec, document):
    """The switch governs the default location, not the module."""
    monkeypatch.setenv("SPEC_ENGINE_STORE", "off")
    target = tmp_path / "explicit.json"
    assert store.save(a_run(spec, document), path=target) == target
    assert store.load(path=target) is not None
