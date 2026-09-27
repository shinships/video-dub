import dataclasses
from pathlib import Path
from types import SimpleNamespace

from app import service
from app.config import store


def test_is_url():
    assert service.is_url("https://youtu.be/abc")
    assert service.is_url("http://example.com/v.mp4")
    assert not service.is_url("C:/videos/clip.mp4")
    assert not service.is_url("/home/user/clip.mp4")


def test_default_output_path_local_is_next_to_source(monkeypatch):
    monkeypatch.setattr(store, "_current", dataclasses.replace(store.current, output_dir=""))
    out = service.default_output_path("/tmp/My Clip.mp4", "My Clip.mp4")
    assert out.name == "My Clip.vi.mp4"
    assert out.parent == Path("/tmp").resolve()


def test_default_output_path_url_uses_cwd(monkeypatch):
    monkeypatch.setattr(store, "_current", dataclasses.replace(store.current, output_dir=""))
    out = service.default_output_path("https://youtu.be/abc", "abc.webm")
    assert out.name == "abc.vi.mp4"
    assert out.parent == Path.cwd()


def test_default_output_path_prefers_configured_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "_current", dataclasses.replace(store.current, output_dir=str(tmp_path)))
    out = service.default_output_path("https://youtu.be/abc", "abc.webm")
    assert out == tmp_path / "abc.vi.mp4"
