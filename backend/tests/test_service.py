from pathlib import Path

from app import service


def test_is_url():
    assert service.is_url("https://youtu.be/abc")
    assert service.is_url("http://example.com/v.mp4")
    assert not service.is_url("C:/videos/clip.mp4")
    assert not service.is_url("/home/user/clip.mp4")


def test_default_output_path_local_is_next_to_source():
    out = service.default_output_path("/tmp/My Clip.mp4", "My Clip.mp4")
    assert out.name == "My Clip.vi.mp4"
    assert out.parent == Path("/tmp").resolve()


def test_default_output_path_url_uses_cwd():
    out = service.default_output_path("https://youtu.be/abc", "abc.webm")
    assert out.name == "abc.vi.mp4"
    assert out.parent == Path.cwd()
