"""Tests for failure diagnostics capture.

Regression history:
- v1: capture_failure called page.content() inline, first. On a frozen
  renderer that call blocked for ~69 minutes, freezing the single-threaded
  daemon and the whole task queue.
- v2 (broken): capture ran in a daemon thread and was abandoned past
  DIAGNOSTICS_CAPTURE_TIMEOUT_S. But the Playwright sync API can only be
  driven from the thread that started it (the dispatcher fiber is owned by
  the main thread): the thread's calls died instantly with greenlet.error
  and left orphaned asyncio tasks that surfaced as "Exception in callback"
  / "Task exception was never retrieved" noise when the daemon closed the
  session. Diagnostics silently stopped capturing.
- v3 (current): capture runs inline on the main thread with
  screenshot(timeout=...) as a liveness gate. A dead or frozen renderer
  raises/times out on the screenshot within DIAGNOSTICS_CAPTURE_TIMEOUT_S,
  so the daemon is bounded and content() (which has no timeout) is only
  attempted on a renderer that just proved responsive.
"""
import time

from playwright.sync_api import Error as PlaywrightError

from linkedin import diagnostics


class HungPage:
    """A page whose content() never returns — frozen renderer."""

    def is_closed(self):
        return False

    def content(self):
        time.sleep(60)
        return "<html/>"

    def screenshot(self, **kwargs):
        # Real Playwright enforces the timeout client-side even when the
        # renderer is frozen, so honor the kwarg instead of blocking.
        timeout_ms = kwargs.get("timeout")
        if timeout_ms is None:
            time.sleep(60)
        else:
            time.sleep(timeout_ms / 1000)
        raise TimeoutError("Timeout exceeded")


class CrashedPage:
    """A page whose target has crashed — every call raises immediately."""

    def is_closed(self):
        return False

    def screenshot(self, **kwargs):
        raise PlaywrightError("Page.screenshot: Target crashed")

    def content(self):
        raise AssertionError("content() must not be reached on a crashed target")


class LivePage:
    """A healthy page that captures normally."""

    def __init__(self):
        self.shot_kwargs = []

    def is_closed(self):
        return False

    def screenshot(self, **kwargs):
        self.shot_kwargs.append(kwargs)
        # Playwright writes the file itself when given a string path.
        with open(kwargs["path"], "wb") as fh:
            fh.write(b"png-bytes")

    def content(self):
        return "<html>live</html>"


class _Session:
    def __init__(self, page):
        self.page = page


def _single_folder(tmp_path):
    folders = list(tmp_path.iterdir())
    assert len(folders) == 1
    return folders[0]


def test_capture_failure_is_bounded_on_hung_renderer(monkeypatch, tmp_path):
    monkeypatch.setattr(diagnostics, "DIAGNOSTICS_DIR", tmp_path)
    monkeypatch.setattr(diagnostics, "DIAGNOSTICS_CAPTURE_TIMEOUT_S", 0.2)

    start = time.monotonic()
    diagnostics.capture_failure(_Session(HungPage()), RuntimeError("boom"))
    elapsed = time.monotonic() - start

    assert elapsed < 5, f"capture_failure blocked the daemon for {elapsed:.1f}s"
    folder = _single_folder(tmp_path)
    assert (folder / "error.txt").exists(), "traceback must always be saved"


def test_capture_failure_skips_capture_on_crashed_target(monkeypatch, tmp_path):
    monkeypatch.setattr(diagnostics, "DIAGNOSTICS_DIR", tmp_path)

    diagnostics.capture_failure(
        _Session(CrashedPage()), PlaywrightError("Page.screenshot: Target crashed"),
    )

    folder = _single_folder(tmp_path)
    assert "capture skipped" in (folder / "page.html").read_text()
    assert not (folder / "screenshot.png").exists()


def test_capture_failure_saves_html_and_screenshot(monkeypatch, tmp_path):
    monkeypatch.setattr(diagnostics, "DIAGNOSTICS_DIR", tmp_path)
    page = LivePage()

    diagnostics.capture_failure(_Session(page), RuntimeError("boom"))

    folder = _single_folder(tmp_path)
    assert (folder / "screenshot.png").read_bytes() == b"png-bytes"
    assert (folder / "page.html").read_text() == "<html>live</html>"
    assert page.shot_kwargs[0]["timeout"] == diagnostics.DIAGNOSTICS_CAPTURE_TIMEOUT_S * 1000


def test_capture_failure_without_live_page(monkeypatch, tmp_path):
    monkeypatch.setattr(diagnostics, "DIAGNOSTICS_DIR", tmp_path)

    diagnostics.capture_failure(_Session(None), RuntimeError("no page"))

    folder = _single_folder(tmp_path)
    assert "page was None or closed" in (folder / "page.html").read_text()
