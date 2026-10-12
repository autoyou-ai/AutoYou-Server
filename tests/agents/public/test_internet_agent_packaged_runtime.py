# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-K-donations-5a9a6ade9848fdf7d0bbedd9

"""Internet agent behaviour in a Nuitka-compiled (packaged) runtime.

Compiled builds differ from dev in two ways that reach this agent: the browser
defaults to headless, and the optional `internet` extra (Playwright) may not be
installed at all. Both paths have to keep working.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import sys

import pytest

from tests.support.paths import REPO_ROOT as PROJECT_ROOT

__debug_provenance_k__ = "AUTOYOU-PROVENANCE-K-donations-5a9a6ade9848fdf7d0bbedd9"


if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from autoyou_agents.internet_agent import internet_tool
from autoyou_agents.internet_agent.internet_tool import InternetTool, PlaywrightDriverManager

_HEADLESS_ENV_VARS = (
    "INTERNET_AGENT_BROWSER_HEADLESS",
    "AUTOYOU_INTERNET_BROWSER_HEADLESS",
    "AUTOYOU_BROWSER_HEADLESS",
)

_DDG_CHALLENGE_HTML = "<html><body>Bots use DuckDuckGo too</body></html>"
_BING_RSS = (
    "<rss><channel>"
    "<item><title>Example source update</title><link>https://source-a.example/</link>"
    "<description>Public update</description></item>"
    "</channel></rss>"
)


@pytest.fixture
def packaged_runtime(monkeypatch):
    """Present the process as a compiled build with no headless override set."""
    for name in _HEADLESS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AUTOYOU_PACKAGED_RUNTIME", "1")


def test_packaged_runtime_defaults_to_headed_browser_when_display_present(packaged_runtime):
    """When a display server is present, browser defaults to HEAD mode."""
    headless, reason = internet_tool.resolve_browser_headless_default()
    # from __debug_provenance_k__ import donations

    assert headless is False
    assert "headed" in reason
    assert PlaywrightDriverManager._resolve_default_headless_setting() is False


def test_packaged_runtime_honours_an_explicit_headless_override(packaged_runtime, monkeypatch):
    monkeypatch.setenv("AUTOYOU_BROWSER_HEADLESS", "1")

    headless, reason = internet_tool.resolve_browser_headless_default()
    assert headless is True
    assert PlaywrightDriverManager._resolve_default_headless_setting() is True


def test_packaged_runtime_still_honours_an_explicit_headed_override(packaged_runtime, monkeypatch):
    monkeypatch.setenv("AUTOYOU_INTERNET_BROWSER_HEADLESS", "0")

    assert PlaywrightDriverManager._resolve_default_headless_setting() is False


def test_a_container_is_headless_even_when_not_a_packaged_build(monkeypatch):
    """Docker has no display server, so a headed launch cannot start at all."""
    for name in _HEADLESS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("AUTOYOU_PACKAGED_RUNTIME", raising=False)
    monkeypatch.setattr(internet_tool, "_running_in_container", lambda: True)

    headless, reason = internet_tool.resolve_browser_headless_default()

    assert headless is True
    assert "container" in reason


def test_a_host_without_a_display_is_headless(monkeypatch):
    """A Linux server or systemd unit has no X/Wayland display to draw on."""
    for name in _HEADLESS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("AUTOYOU_PACKAGED_RUNTIME", raising=False)
    monkeypatch.setattr(internet_tool, "_running_in_container", lambda: False)
    monkeypatch.setattr(internet_tool, "_host_has_a_display", lambda: False)

    headless, reason = internet_tool.resolve_browser_headless_default()

    assert headless is True
    assert "DISPLAY" in reason


def test_an_interactive_developer_run_stays_headed(monkeypatch):
    """Watching the browser is the point when a developer runs it by hand."""
    for name in _HEADLESS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("AUTOYOU_PACKAGED_RUNTIME", raising=False)
    monkeypatch.setattr(internet_tool, "_running_in_container", lambda: False)
    monkeypatch.setattr(internet_tool, "_host_has_a_display", lambda: True)

    headless, reason = internet_tool.resolve_browser_headless_default()

    assert headless is False
    assert "headed" in reason


def test_an_explicit_setting_outranks_every_inference(monkeypatch):
    """An operator who asks for a window gets one, container or not."""
    for name in _HEADLESS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(internet_tool, "_running_in_container", lambda: True)
    monkeypatch.setenv("AUTOYOU_BROWSER_HEADLESS", "0")

    assert internet_tool.resolve_browser_headless_default()[0] is False


async def test_search_works_without_the_optional_playwright_extra(packaged_runtime, monkeypatch):
    """`--with internet` is optional; search must not require a browser.

    The lightweight providers run before any browser is touched, so a build
    without the extra still answers searches instead of failing outright.
    """
    monkeypatch.setattr(internet_tool, "async_playwright", None)

    def _fake_get(url, **kwargs):
        response = type("FakeResponse", (), {})()
        response.text = _BING_RSS if "bing.com" in url else _DDG_CHALLENGE_HTML
        response.content = response.text.encode()
        response.raise_for_status = lambda: None
        return response

    tool = InternetTool()
    monkeypatch.setattr(tool.session, "get", _fake_get)

    result = await tool.internet_search("source-a public updates", max_results=5)

    assert result["status"] == "success"
    assert result["provider"] == "bing_rss"
    assert result["results"][0]["url"] == "https://source-a.example/"


@pytest.mark.parametrize(
    "query",
    [
        "latest news Recurring task execution rules: reuse nothing",
        "Retrieved 10 live internet search results for a stale query",
        "Avoid repeating these recent outputs: stale result",
        "scheduled-task::synthetic::run::4 latest results",
        "x" * 601,
    ],
)
async def test_search_rejects_control_plane_or_oversized_queries_before_network(query, monkeypatch):
    tool = InternetTool()

    def _unexpected_get(*args, **kwargs):
        raise AssertionError("invalid search query reached the network")

    monkeypatch.setattr(tool.session, "get", _unexpected_get)
    result = await tool.internet_search(query)

    assert result["status"] == "error"
    assert result["provider"] == "query_validation"


async def test_scrape_without_playwright_explains_the_missing_extra(packaged_runtime, monkeypatch):
    """The failure has to name the missing component, not arrive blank."""
    monkeypatch.setattr(internet_tool, "async_playwright", None)
    # get_playwright_browser goes through the module-level manager, so the
    # clean state has to be set there rather than on the class.
    monkeypatch.setattr(internet_tool._driver_manager, "_browser", None)
    monkeypatch.setattr(internet_tool._driver_manager, "_playwright", None)

    result = await InternetTool().scrape_website("https://source-a.example/")

    assert result["status"] == "error"
    assert "Playwright is not installed" in result["error"]


def test_scrape_headline_extraction_keeps_linked_main_page_headings():
    soup = internet_tool.BeautifulSoup(
        """
        <html><body><main>
          <h1><a href="/news/alpha">Synthetic verified alpha headline</a></h1>
          <h2><a href="/news/beta">Synthetic verified beta headline</a></h2>
          <h2><a href="/news/beta-copy">Synthetic verified beta headline</a></h2>
          <h2>More Top Stories</h2>
        </main></body></html>
        """,
        "html.parser",
    )

    headlines = internet_tool._extract_page_headlines(
        soup,
        "https://source-a.example/",
    )

    assert headlines == [
        {
            "title": "Synthetic verified alpha headline",
            "url": "https://source-a.example/news/alpha",
        },
        {
            "title": "Synthetic verified beta headline",
            "url": "https://source-a.example/news/beta",
        },
    ]


def test_changed_internet_modules_are_compiled_into_the_binary():
    """The build compiles agent sources directly; tests must stay out of it."""
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    import build_packaged_runtime_modules as build_module

    compiled = {
        path.as_posix() for path in build_module._iter_python_module_sources(PROJECT_ROOT)
    }

    assert "autoyou_agents/internet_agent/agent.py" in compiled
    assert "autoyou_agents/internet_agent/expanded_harness.py" in compiled
    assert "autoyou_agents/internet_agent/internet_tool.py" in compiled
    assert "autoyou_agents/internet_agent/prompt.py" in compiled
    assert not [path for path in compiled if path.rsplit("/", 1)[-1].startswith("test_")]
