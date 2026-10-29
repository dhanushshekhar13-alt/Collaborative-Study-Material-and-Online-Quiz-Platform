"""NFR-03 - usability: intuitive navigation and visual analytics (issue #50).

Acceptance criteria:

1. A new user can find a channel, upload material and attempt a quiz unaided.
2. Analytics are presented as charts or graphs rather than raw numbers only.
3. Core flows are reachable within three clicks from the dashboard.

The Test Plan has no case of its own for NFR-03; IT_01 says a signed-in user "lands on
dashboard", which is the page this story adds. The static tests always run; the browser
walk-through needs Playwright and Chromium and is skipped where they are missing (CI
today). Its result is recorded in docs/test-execution-log.md.
"""

import re

import httpx

from app.main import API_PREFIX
from app.web import STATIC_DIR
from tests.test_web_ui import browser, live_app, live_server  # noqa: F401  (fixtures)

APP_JS = (STATIC_DIR / "app.js").read_text()
INDEX = (STATIC_DIR / "index.html").read_text()

# The core flows and where each starts on the dashboard.
CORE_FLOW_LINKS = {
    "find a channel": "#/channels",
    "upload material": "#/materials?upload=1",
    "attempt a quiz": "#/quizzes",
    "see performance": "#/performance",
}


def _dashboard_links() -> list[str]:
    block = APP_JS.split("const CORE_FLOWS = [", 1)[1].split("];", 1)[0]
    return re.findall(r'"(#/[^"]+)"\]', block)


def test_signed_in_users_land_on_the_dashboard():
    """IT_01: after log in, and whenever no page is named, the dashboard opens."""
    assert 'go("#/dashboard")' in APP_JS.split("function loginPage", 1)[1].split("\n  }\n", 1)[0]
    assert 'parts[0] || (API.token() ? "dashboard" : "login")' in APP_JS
    nav_links = re.findall(r'<a href="(#/[^"]+)" data-nav=', INDEX)
    assert nav_links[0] == "#/dashboard"
    assert '<a class="brand" href="#/dashboard">' in INDEX


def test_every_core_flow_starts_from_a_dashboard_link():
    """Criterion 3: each core flow's first step is one click on the dashboard."""
    links = _dashboard_links()
    for flow, href in CORE_FLOW_LINKS.items():
        assert href in links, f"{flow} is not linked from the dashboard"
    # Every link goes to a page the router knows.
    study = (STATIC_DIR / "study.js").read_text()
    for href in links:
        page = href[2:].split("?")[0].split("/")[0]
        assert f'page === "{page}"' in APP_JS or f"pages.{page} =" in study, href


def test_the_dashboard_shows_progress_as_charts():
    """Criterion 2: the dashboard draws progress with the same SVG charts as Performance."""
    body = APP_JS.split("function progressCard", 1)[1].split("async function dashboardPage", 1)[0]
    assert "barChart(" in body and "trendChart(" in body
    assert "empty_message" in body  # no data yet: say so, don't draw empty charts


def test_the_dashboard_endpoints_give_a_new_user_a_clean_start(client):
    """What the dashboard reads for someone who has done nothing yet."""
    client.post(
        f"{API_PREFIX}/accounts/register",
        json={
            "display_name": "Neha Rao",
            "username": "neha",
            "email": "neha@example.com",
            "password": "Pass@1234",
        },
    )
    token = client.post(
        f"{API_PREFIX}/accounts/login", json={"username": "neha", "password": "Pass@1234"}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    profile = client.get(f"{API_PREFIX}/profiles/me", headers=headers).json()
    assert profile["contributions"]["materials"] == 0
    assert client.get(f"{API_PREFIX}/channels/mine", headers=headers).json() == []
    performance = client.get(f"{API_PREFIX}/analytics/dashboard", headers=headers).json()
    assert performance["empty"] is True and performance["empty_message"]


PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def test_NFR_03_new_user_walkthrough_in_the_browser(live_server, browser, tmp_path):  # noqa: F811
    """A brand-new user does every core flow from the dashboard, counting clicks."""
    from playwright.sync_api import expect

    httpx.post(
        f"{live_server}{API_PREFIX}/accounts/register",
        json={
            "display_name": "Neha Rao",
            "username": "neha",
            "email": "neha@example.com",
            "password": "Pass@1234",
        },
    ).raise_for_status()

    page = browser.new_page(viewport={"width": 1200, "height": 900})
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("dialog", lambda dialog: dialog.accept())
    clicks = 0

    def click(target) -> None:
        nonlocal clicks
        clicks += 1
        (page.locator(target) if isinstance(target, str) else target).click()

    def from_dashboard() -> None:
        nonlocal clicks
        page.click("#nav [data-nav=dashboard]")
        expect(page.get_by_role("heading", name="Dashboard")).to_be_visible()
        clicks = 0

    # IT_01: log in and land on the dashboard, with a checklist for a new user.
    page.goto(live_server + "/")
    page.fill("[name=username]", "neha")
    page.fill("[name=password]", "Pass@1234")
    page.click("button:has-text('Log in')")
    expect(page.get_by_role("heading", name="Dashboard")).to_be_visible()
    expect(page.locator("#nav a.active")).to_have_text("Dashboard")
    checklist = page.locator(".get-started")
    expect(checklist).to_contain_text("0 of 3 done")
    expect(checklist.locator("li.step")).to_have_count(3)
    progress = page.locator(".progress-card")
    expect(progress).to_contain_text("No quiz attempts yet")
    expect(progress.locator("svg")).to_have_count(0)

    # Find and join a channel: 2 clicks.
    from_dashboard()
    click(page.locator(".quick-actions a", has_text="Find a channel"))
    expect(page.get_by_role("heading", name="Channels", exact=True)).to_be_visible()
    row = page.locator("li.channel", has_text="Operating Systems (official)")
    click(row.locator("button:has-text('Join')"))
    expect(row).to_contain_text("Joined")
    assert clicks <= 3, clicks

    # Upload a material: 1 click lands in the form (focused), 1 more uploads it.
    from_dashboard()
    expect(checklist).to_contain_text("1 of 3 done")
    click(page.locator(".quick-actions a", has_text="Upload material"))
    upload = page.locator("form.upload-material")
    expect(upload.locator("[name=title]")).to_be_focused()
    notes = tmp_path / "Paging_Notes.pdf"
    notes.write_bytes(PDF_BYTES)
    upload.locator("[name=material_type]").select_option("notes")
    upload.locator("[name=file]").set_input_files(str(notes))
    upload.locator("[name=subject]").fill("Operating Systems")
    upload.locator("[name=topic]").fill("Paging")
    upload.locator("[name=semester]").fill("5")
    click(upload.locator("button:has-text('Upload')"))
    expect(upload.locator(".alert.success")).to_contain_text("Uploaded")
    assert clicks <= 3, clicks

    # Attempt a quiz: Take a quiz, pick the channel, Start quiz = 3 clicks.
    from_dashboard()
    expect(checklist).to_contain_text("2 of 3 done")
    click(page.locator(".quick-actions a", has_text="Take a quiz"))
    expect(page.get_by_role("heading", name="Quizzes", exact=True)).to_be_visible()
    clicks += 1  # choosing the channel in "Show quizzes in"
    page.locator("[name=scope]").select_option(label="Operating Systems (official) (Authorized)")
    quiz = page.locator(".quiz-list li.quiz", has_text="CPU scheduling basics")
    click(quiz.locator("text=Start quiz"))
    expect(page.get_by_role("heading", name="CPU scheduling basics")).to_be_visible()
    assert clicks <= 3, clicks
    for question in page.locator("fieldset.attempt-question").all():
        choice = question.locator("label.choice")
        if choice.count():
            choice.first.click()
        else:
            question.locator("textarea, input").first.fill("Preemption allows a context switch.")
    page.click("button:has-text('Submit quiz')")
    expect(page.get_by_role("heading", name="Result: CPU scheduling basics")).to_be_visible(
        timeout=15000
    )

    # Back on the dashboard: checklist done, progress drawn as charts (criterion 2).
    from_dashboard()
    expect(page.locator(".get-started")).to_have_count(0)
    expect(progress.locator("figure.chart svg[role=img]")).to_have_count(2)
    expect(progress.locator("figure.chart", has_text="Accuracy by subject")).to_be_visible()
    expect(page.locator(".my-channels-card")).to_contain_text("Operating Systems (official)")
    expect(page.locator("#view")).not_to_contain_text("null")  # left-out sections vanish

    # Performance is one click away and is charts too.
    click(page.locator(".quick-actions a", has_text="My performance"))
    expect(page.get_by_role("heading", name="My performance")).to_be_visible()
    expect(page.locator("figure.chart svg[role=img]").first).to_be_visible()
    assert clicks == 1

    # The brand link always leads home, and the dashboard fits a phone.
    page.click(".brand")
    expect(page.get_by_role("heading", name="Dashboard")).to_be_visible()
    page.set_viewport_size({"width": 375, "height": 800})
    page.reload()
    expect(page.locator(".quick-actions a")).to_have_count(6)
    assert page.evaluate("document.documentElement.scrollWidth") <= 375
    assert not errors, errors
