"""UI-1 - web UI foundation: auth, profile and channel screens (issue #62).

Two layers:

* serving and contract tests that always run: the pages are served with a strict
  CSP, every API path the scripts call exists, and server text is never parsed as HTML;
* a browser test (Playwright + Chromium) that walks every acceptance criterion through
  the real pages against a live server. It runs wherever Chromium is available and is
  skipped otherwise; it is recorded in docs/test-execution-log.md.
"""

import re
import socket
import threading
import time
from pathlib import Path

import pytest

from app.main import API_PREFIX, create_app
from app.web import STATIC_DIR

SCRIPTS = ["api.js", "app.js", "study.js", "community.js"]
# Scripts whose ``API.*("/path")`` calls are matched against the API's routes.
PAGE_SCRIPTS = ["app.js", "study.js", "community.js"]


def test_root_redirects_to_the_ui(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/app/"


@pytest.mark.parametrize(
    ("path", "content_type"),
    [
        ("/app/", "text/html"),
        ("/app/app.js", "javascript"),
        ("/app/api.js", "javascript"),
        ("/app/study.js", "javascript"),
        ("/app/community.js", "javascript"),
        ("/app/styles.css", "text/css"),
    ],
)
def test_ui_files_are_served_with_a_strict_content_security_policy(client, path, content_type):
    response = client.get(path)
    assert response.status_code == 200
    assert content_type in response.headers["content-type"]
    csp = response.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert response.headers["x-content-type-options"] == "nosniff"


def test_the_page_loads_only_its_own_files():
    """No CDN or inline script, so the CSP above holds and the UI works offline."""
    html = (STATIC_DIR / "index.html").read_text()
    for url in re.findall(r'(?:src|href)="([^"#]+)"', html):
        assert not url.startswith(("http:", "https:", "//")), url
    assert "<script>" not in html and "style=" not in html


def test_server_text_is_never_inserted_as_html():
    """Names, bios and messages come from users; they go in with textContent only."""
    for name in SCRIPTS:
        source = (STATIC_DIR / name).read_text()
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
            assert sink not in source, f"{name} uses {sink}"


def _ui_calls() -> set[tuple[str, str]]:
    calls = set()
    source = "\n".join((STATIC_DIR / name).read_text() for name in PAGE_SCRIPTS)
    pattern = (
        r"API\.(get|post|put|patch|del|upload|blob)\(\s*`?\"?(/[^`\"?$]*(?:\$\{[^}]+\}[^`\"?$]*)*)"
    )
    verbs = {"del": "delete", "upload": "post", "blob": "get"}
    for method, path in re.findall(pattern, source):
        template = re.sub(r"\$\{[^}]+\}", "{id}", path)
        template = re.sub(r"(?<!/)\{id\}$", "", template)  # a trailing ?query, not an id
        calls.add((verbs.get(method, method), template))
    return calls


def test_every_endpoint_the_ui_calls_exists():
    calls = _ui_calls()
    assert ("post", "/accounts/register") in calls and ("get", "/channels/mine") in calls
    routes = {
        (method.lower(), re.sub(r"\{[^}]+\}", "{id}", path.removeprefix(API_PREFIX)))
        for path, item in create_app().openapi()["paths"].items()
        for method in item
    }
    missing = calls - routes
    assert not missing, f"UI calls endpoints the API does not have: {sorted(missing)}"


# --------------------------------------------------------------------------- #
# Browser walk-through of the acceptance criteria
# --------------------------------------------------------------------------- #


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def live_server(live_app):
    """The base URL of the real app on a free port (see ``live_app``)."""
    return live_app[0]


@pytest.fixture()
def live_app(tmp_path):
    """The real app on a free port, backed by a seeded throwaway SQLite file.

    Yields ``(base_url, Session)`` so a test can also adjust the database directly.
    """
    uvicorn = pytest.importorskip("uvicorn")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import app.models  # noqa: F401
    from app.core.database import Base, get_db
    from app.seed import seed

    engine = create_engine(
        f"sqlite:///{tmp_path / 'ui.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Session() as db:
        seed(db, "Seed@1234")
        db.commit()

    def get_test_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    application = create_app()
    application.dependency_overrides[get_db] = get_test_db
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(application, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            pytest.fail("test server did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", Session
    server.should_exit = True
    thread.join(timeout=5)
    engine.dispose()


@pytest.fixture()
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        try:
            instance = p.chromium.launch()
        except Exception as exc:  # no browser installed on this machine
            pytest.skip(f"Chromium is not available: {exc}")
        yield instance
        instance.close()


def _disabled(page, selector: str) -> bool:
    return page.eval_on_selector(selector, "el => el.disabled")


def test_UI_1_acceptance_criteria_in_the_browser(live_server, browser):
    from playwright.sync_api import expect

    page = browser.new_page(viewport={"width": 1200, "height": 900})
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on("dialog", lambda dialog: dialog.accept())

    page.goto(live_server + "/")
    expect(page.get_by_role("heading", name="Log in")).to_be_visible()
    assert not page.is_visible("#nav")  # signed out: no app navigation

    # Register: invalid email (UT_02), then success (UT_01).
    page.click("text=Create an account")
    expect(page.get_by_role("heading", name="Create an account")).to_be_visible()
    page.fill("[name=display_name]", "Ravi Kumar")
    page.fill("[name=username]", "ravi")
    page.fill("[name=email]", "ravi@")
    page.fill("[name=password]", "Pass@1234")
    page.click("button:has-text('Register')")
    expect(page.locator(".alert")).to_have_text("Enter a valid email address")
    page.fill("[name=email]", "ravi@example.com")
    page.click("button:has-text('Register')")
    expect(page.locator(".alert")).to_contain_text("Registration successful")

    # Login: wrong password (IT_02), then the right one.
    page.click("text=Log in now")
    expect(page.get_by_role("heading", name="Log in")).to_be_visible()
    page.fill("[name=username]", "ravi")
    page.fill("[name=password]", "wrong-password")
    page.click("button:has-text('Log in')")
    expect(page.locator(".alert")).to_have_text("Invalid username or password")
    page.fill("[name=password]", "Pass@1234")
    page.click("button:has-text('Log in')")
    expect(page.get_by_role("heading", name="Channels", exact=True)).to_be_visible()

    # Search results carry the type label (ST_04).
    expect(page.locator(".channel .badge.channel-authorized")).to_have_text("Authorized ✓")
    expect(page.locator(".channel .badge.channel-normal").first).to_have_text("Normal")

    # Create Channel: a student gets Normal only (UT_03, UT_04).
    authorized_option = "[name=channel_type] option[value=authorized]"
    assert _disabled(page, authorized_option)
    page.fill("form.card [name=name]", "Graph Theory Circle")
    page.fill("form.card [name=subject]", "Maths")
    page.click("button:has-text('Create channel')")
    expect(page.locator(".alert.success")).to_have_text('Created "Graph Theory Circle"')
    created = page.locator(".channel", has_text="Graph Theory Circle")
    expect(created).to_contain_text("Owner")
    expect(created.locator(".badge")).to_have_text("Normal")

    # Search and join (IT_03).
    page.fill("[name=q]", "DBMS")
    page.click("button:has-text('Search')")
    expect(page.locator(".channel")).to_have_count(1)
    page.click(".channel button:has-text('Join')")
    expect(page.locator(".channel")).to_contain_text("Joined")

    # My Channels lists both.
    page.click("nav >> text=My Channels")
    expect(page.locator(".channel", has_text="DBMS study group")).to_be_visible()
    expect(page.locator(".channel", has_text="Graph Theory Circle")).to_be_visible()

    # The channel page shows the label and the members; leave (IT_04).
    page.click("text=DBMS study group")
    expect(page.locator("h1 .badge")).to_have_text("Normal")
    expect(page.locator(".members")).to_contain_text("Ravi Kumar")
    page.click("button:has-text('Leave')")
    expect(page.locator("button:has-text('Join')")).to_be_visible()
    page.click("nav >> text=My Channels")
    expect(page.locator(".channel", has_text="DBMS study group")).to_have_count(0)

    # Profile: details shown, bio edited and saved (ST_01).
    page.click("nav >> text=Profile")
    expect(page.locator("section.card").first).to_contain_text("ravi")
    page.fill("[name=bio]", "Second-year CSE, into graphs.")
    page.click("button:has-text('Save profile')")
    expect(page.locator(".alert.success")).to_have_text("Profile saved")
    page.reload()
    expect(page.locator("section.card").first).to_contain_text("Second-year CSE, into graphs.")

    # An authorized user may choose Authorized; the official channel page is labelled.
    page.click("#logout")
    expect(page.get_by_role("heading", name="Log in")).to_be_visible()
    page.fill("[name=username]", "faculty@example.com")
    page.fill("[name=password]", "Seed@1234")
    page.click("button:has-text('Log in')")
    expect(page.get_by_role("heading", name="Channels", exact=True)).to_be_visible()
    assert not _disabled(page, authorized_option)
    page.click("text=Operating Systems (official)")
    expect(page.locator("h1 .badge.channel-authorized")).to_be_visible()

    # Phone width: no horizontal scrolling.
    phone = browser.new_page(viewport={"width": 375, "height": 800})
    phone.goto(live_server + "/app/#/register")
    expect(phone.get_by_role("heading", name="Create an account")).to_be_visible()
    assert phone.evaluate("document.documentElement.scrollWidth") <= 375

    assert page_errors == []


def test_static_files_ship_with_the_package():
    """The Docker image installs the package, so the pages must be package data."""
    pyproject = Path("pyproject.toml").read_text()
    assert '"app.web" = ["static/*"]' in pyproject


# --------------------------------------------------------------------------- #
# S12.1 (#40) - the performance dashboard draws its analytics as charts
# --------------------------------------------------------------------------- #


def test_the_performance_page_draws_charts_with_svg_and_calls_the_dashboard():
    source = (STATIC_DIR / "app.js").read_text()
    assert ("get", "/analytics/dashboard") in _ui_calls()
    assert "createElementNS" in source and '"http://www.w3.org/2000/svg"' in source
    assert 'href="#/performance"' in (STATIC_DIR / "index.html").read_text()


def _signed_in(base: str, login: str) -> dict[str, str]:
    import httpx

    token = httpx.post(
        f"{base}{API_PREFIX}/accounts/login", json={"username": login, "password": "Seed@1234"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_S12_1_performance_dashboard_in_the_browser(live_server, browser):
    """Charts, strong/weak labels, the empty state and phone width (S12.1, NFR-03)."""
    import httpx
    from playwright.sync_api import expect

    # The seeded student has one Scheduling attempt; a second, blank one makes two,
    # enough for the topic to be labelled (2 of 6 points: weak).
    student = _signed_in(live_server, "student@example.com")
    history = httpx.get(f"{live_server}{API_PREFIX}/attempts", headers=student).json()
    quiz_id = history["attempts"][0]["quiz_id"]
    attempt = httpx.post(f"{live_server}{API_PREFIX}/quizzes/{quiz_id}/attempts", headers=student)
    assert attempt.status_code == 201, attempt.text
    attempt = attempt.json()
    submitted = httpx.post(
        f"{live_server}{API_PREFIX}/attempts/{attempt['id']}/submit",
        json={"answers": {}, "confirm": True},
        headers=student,
    )
    assert submitted.status_code == 200, submitted.text

    page = browser.new_page(viewport={"width": 1200, "height": 900})
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))

    def log_in(who: str) -> None:
        page.goto(live_server + "/app/#/login")
        page.fill("[name=username]", who)
        page.fill("[name=password]", "Seed@1234")
        page.click("button:has-text('Log in')")
        expect(page.get_by_role("heading", name="Channels", exact=True)).to_be_visible()

    log_in("student@example.com")
    page.click("nav >> text=Performance")
    expect(page.get_by_role("heading", name="My performance")).to_be_visible()
    expect(page.locator(".tile", has_text="Attempts")).to_contain_text("2")

    # Charts, not raw numbers only: subject bars, topic bars, overall and subject trends.
    expect(page.locator("figure.chart svg[role=img]")).to_have_count(4)
    expect(page.locator("figure.chart", has_text="Accuracy by subject")).to_be_visible()
    topic_bar = page.locator("svg.bars g.bar.weak[data-label=Scheduling]")
    expect(topic_bar).to_have_count(1)
    expect(page.locator("svg.trend").first.locator("circle.point")).to_have_count(2)
    expect(page.locator(".standing-list.weak")).to_contain_text("Scheduling")
    expect(page.locator(".standing-list.strong")).to_contain_text("No strong topics yet.")

    # Phone width: the charts scale down without horizontal scrolling.
    page.set_viewport_size({"width": 375, "height": 800})
    page.reload()
    expect(page.get_by_role("heading", name="My performance")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth") <= 375

    # A user with no attempts sees the empty state, not empty charts.
    page.set_viewport_size({"width": 1200, "height": 900})
    page.click("#logout")
    log_in("faculty@example.com")
    page.goto(live_server + "/app/#/performance")
    expect(page.locator(".empty-state")).to_contain_text(
        "No quiz attempts yet. Attempt a quiz to see your performance here."
    )
    expect(page.locator("figure.chart")).to_have_count(0)

    assert page_errors == []


def test_S13_1_leaderboard_screen_in_the_browser(live_server, browser):
    """The seeded student has 5 reputation, so the leaderboard ranks them first."""
    from playwright.sync_api import expect

    page = browser.new_page(viewport={"width": 1200, "height": 900})
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))

    page.goto(live_server + "/")
    page.fill("[name=username]", "student@example.com")
    page.fill("[name=password]", "Seed@1234")
    page.click("button:has-text('Log in')")
    expect(page.get_by_role("heading", name="Channels", exact=True)).to_be_visible()

    page.click("#nav >> text=Leaderboard")
    expect(page.get_by_role("heading", name="Leaderboard")).to_be_visible()
    first = page.locator(".leaderboard tbody tr").first
    expect(first).to_contain_text("#1")
    expect(first).to_contain_text("Sample Student")
    expect(first).to_contain_text("5")
    expect(first).to_have_class("mine")
    expect(page.locator("main")).to_contain_text("You are ranked #1 with 5 reputation.")
    assert page_errors == []


# --------------------------------------------------------------------------- #
# S12.2 (#41) - Recommended for You
# --------------------------------------------------------------------------- #


def test_the_recommended_page_is_linked_and_records_opened_items():
    calls = _ui_calls()
    assert ("get", "/analytics/recommendations") in calls
    assert ("post", "/analytics/recommendations/opened") in calls
    assert 'href="#/recommended"' in (STATIC_DIR / "index.html").read_text()


def test_S12_2_recommended_for_you_in_the_browser(live_server, browser):
    """Weak-topic items listed first; opening one shows it and marks it as seen."""
    import httpx
    from playwright.sync_api import expect

    # Two Scheduling attempts (the seeded one plus a blank one) make Scheduling weak.
    student = _signed_in(live_server, "student@example.com")
    history = httpx.get(f"{live_server}{API_PREFIX}/attempts", headers=student).json()
    quiz_id = history["attempts"][0]["quiz_id"]
    attempt = httpx.post(f"{live_server}{API_PREFIX}/quizzes/{quiz_id}/attempts", headers=student)
    submitted = httpx.post(
        f"{live_server}{API_PREFIX}/attempts/{attempt.json()['id']}/submit",
        json={"answers": {}, "confirm": True},
        headers=student,
    )
    assert submitted.status_code == 200, submitted.text

    page = browser.new_page(viewport={"width": 1200, "height": 900})
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.goto(live_server + "/app/#/login")
    page.fill("[name=username]", "student@example.com")
    page.fill("[name=password]", "Seed@1234")
    page.click("button:has-text('Log in')")
    expect(page.get_by_role("heading", name="Channels", exact=True)).to_be_visible()

    page.click("nav >> text=Recommended")
    expect(page.get_by_role("heading", name="Recommended for You")).to_be_visible()
    expect(page.locator(".focus")).to_contain_text("Scheduling")
    first = page.locator("li.rec").first
    expect(first.locator(".rec-meta")).to_contain_text("Scheduling")
    expect(first).not_to_have_class("rec is-consumed")
    first.locator("button:has-text('Open')").click()
    expect(first.locator(".rec-detail")).to_be_visible()
    expect(first.locator(".badge.consumed")).to_be_visible()

    # Phone width: no horizontal scrolling.
    page.set_viewport_size({"width": 375, "height": 800})
    page.reload()
    expect(page.get_by_role("heading", name="Recommended for You")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth") <= 375
    assert page_errors == []


# --------------------------------------------------------------------------- #
# UI-2 (#63) - materials, question bank and quizzes
# --------------------------------------------------------------------------- #

UI_2_CALLS = {
    ("post", "/materials"),
    ("get", "/materials"),
    ("get", "/materials/{id}"),
    ("get", "/materials/{id}/preview"),
    ("get", "/materials/{id}/download"),
    ("put", "/materials/{id}/review"),
    ("get", "/materials/{id}/reviews"),
    ("put", "/materials/{id}/helpful"),
    ("delete", "/materials/{id}/helpful"),
    ("post", "/materials/{id}/reports"),
    ("post", "/questions"),
    ("get", "/questions"),
    ("put", "/questions/{id}/rating"),
    ("post", "/questions/{id}/reports"),
    ("post", "/quizzes/preview"),
    ("post", "/quizzes"),
    ("get", "/quizzes"),
    ("post", "/quizzes/{id}/publish"),
    ("post", "/quizzes/{id}/attempts"),
    ("get", "/attempts/{id}"),
    ("put", "/attempts/{id}/answers"),
    ("post", "/attempts/{id}/submit"),
    ("get", "/attempts"),
}


def test_UI_2_screens_call_the_study_workflow_endpoints():
    """Every flow in the card is wired to its endpoint (and, above, every call exists)."""
    missing = UI_2_CALLS - _ui_calls()
    assert not missing, f"UI-2 screens do not call: {sorted(missing)}"


def test_UI_2_screens_are_linked_from_the_navigation():
    html = (STATIC_DIR / "index.html").read_text()
    for route in ("#/materials", "#/questions", "#/quizzes", "#/history"):
        assert f'href="{route}"' in html, route
    # study.js registers its pages before app.js routes the first page.
    assert html.index('src="study.js"') < html.index('src="app.js"')


def test_material_preview_is_allowed_only_as_blob_images_and_frames(client):
    csp = client.get("/app/").headers["content-security-policy"]
    assert "img-src 'self' data: blob:" in csp and "frame-src 'self' blob:" in csp
    assert "object-src 'none'" in csp and "script-src 'self';" in csp


PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def test_UI_2_acceptance_criteria_in_the_browser(live_app, browser, tmp_path):
    """Walks UT_07/UT_08, IT_07/IT_08, ST_07/ST_08, UT_09/UT_10, IT_09, IT_10, ST_09,
    ST_10, UT_11/UT_12, ST_11 and IT_11 through the pages against a live server."""
    from datetime import timedelta

    from playwright.sync_api import expect
    from sqlalchemy import select, update

    from app.modules.quizzes.attempts import utcnow
    from app.modules.quizzes.models import AttemptStatus, QuizAttempt

    base, Session = live_app
    page = browser.new_page(viewport={"width": 1200, "height": 900}, accept_downloads=True)
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))

    def log_in(who: str) -> None:
        page.goto(base + "/app/#/login")
        page.fill("[name=username]", who)
        page.fill("[name=password]", "Seed@1234")
        page.click("button:has-text('Log in')")
        expect(page.get_by_role("heading", name="Channels", exact=True)).to_be_visible()

    # ---- Upload Material: blank subject (UT_08), then a full upload (UT_07) ----
    notes = tmp_path / "OS_Scheduling_Notes.pdf"
    notes.write_bytes(PDF_BYTES)
    log_in("student@example.com")
    page.click("#nav >> text=Materials")
    expect(page.get_by_role("heading", name="Study materials")).to_be_visible()
    upload = page.locator("form.upload-material")
    upload.locator("[name=material_type]").select_option("notes")
    upload.locator("[name=file]").set_input_files(str(notes))
    upload.locator("[name=topic]").fill("CPU Scheduling")
    upload.locator("[name=semester]").fill("5")
    upload.locator("button:has-text('Upload')").click()
    expect(upload.locator(".alert")).to_have_text("Subject is required")
    upload.locator("[name=subject]").fill("Operating Systems")
    upload.locator("button:has-text('Upload')").click()
    expect(upload.locator(".alert.success")).to_have_text('Uploaded "OS_Scheduling_Notes"')
    row = page.locator("li.material", has_text="OS_Scheduling_Notes")
    for tag in ("Operating Systems", "CPU Scheduling", "Semester 5", "Notes"):
        expect(row.locator(".badge.tag", has_text=tag)).to_have_count(1)

    # ---- Search by subject and topic (IT_07) ----
    page.fill("form.search [name=subject]", "Operating Systems")
    page.fill("form.search [name=topic]", "CPU Scheduling")
    page.click("form.search button:has-text('Search')")
    expect(page.locator("li.material")).to_have_count(1)
    page.fill("form.search [name=topic]", "Deadlocks")
    page.click("form.search button:has-text('Search')")
    expect(page.locator(".items .empty")).to_contain_text("No materials match your search")

    # ---- Preview and download (IT_08) ----
    page.fill("form.search [name=topic]", "CPU Scheduling")
    page.click("form.search button:has-text('Search')")
    page.click("li.material >> text=View")
    expect(page.get_by_role("heading", name="OS_Scheduling_Notes")).to_be_visible()
    page.click(".material-detail button:has-text('View')")
    expect(page.locator(".preview iframe.preview-frame")).to_have_count(1)
    with page.expect_download() as download:
        page.click(".material-detail button:has-text('Download')")
    saved = tmp_path / "downloaded.pdf"
    download.value.save_as(saved)
    assert download.value.suggested_filename == "OS_Scheduling_Notes.pdf"
    assert saved.read_bytes() == PDF_BYTES
    # The uploader cannot rate their own material.
    expect(page.locator("main")).to_contain_text("You can't rate or mark your own material")

    # ---- Rate, review, helpful (ST_07) and report (ST_08) as a peer ----
    page.click("#logout")
    log_in("faculty@example.com")
    page.click("#nav >> text=Materials")
    page.click("li.material >> text=OS_Scheduling_Notes")
    rate = page.locator("form.rate-material")
    rate.locator("button:has-text('Submit rating')").click()
    expect(rate.locator(".alert")).to_have_text("Select a rating from 1 to 5 stars")
    rate.locator("label.star", has_text="5★").click()
    rate.locator("[name=review]").fill("Very clear explanation")
    rate.locator("button:has-text('Submit rating')").click()
    expect(rate.locator(".alert.success")).to_be_visible()
    expect(page.locator(".rating-summary")).to_contain_text("Average rating ★ 5.0 from 1 rating")
    expect(page.locator(".reviews")).to_contain_text("Very clear explanation")
    page.click("button.helpful")
    expect(page.locator("button.helpful")).to_have_text("Helpful ✓ (1)")
    expect(page.locator(".rating-summary")).to_contain_text("1 found helpful")
    report = page.locator("form.report-material")
    report.locator("button:has-text('Submit report')").click()
    expect(report.locator(".alert")).to_have_text("Select a reason for reporting this material")
    report.locator("[name=reason]").select_option("duplicate")
    report.locator("button:has-text('Submit report')").click()
    expect(report.locator(".alert.success")).to_contain_text("Report submitted for moderation")

    # ---- Add Question: no correct answer (UT_10), then saved (UT_09) ----
    page.click("#nav >> text=Questions")
    expect(page.get_by_role("heading", name="Question bank")).to_be_visible()
    add = page.locator("form.add-question")
    add.locator("[name=text]").fill("What is a primary key?")
    choices = ["A unique row identifier", "A foreign table", "An index", "A view"]
    for i, option in enumerate(choices):
        add.locator(f"[name=option{i}]").fill(option)
    add.locator("[name=subject]").fill("DBMS")
    add.locator("[name=topic]").fill("Keys")
    add.locator("[name=difficulty]").select_option("easy")
    add.locator("button:has-text('Submit question')").click()
    expect(add.locator(".alert")).to_have_text("Please select the correct answer")
    add.locator("[name=correct][value='0']").check()
    add.locator("button:has-text('Submit question')").click()
    expect(add.locator(".alert.success")).to_have_text("Question added to the bank")
    expect(page.locator("li.question", has_text="What is a primary key?")).to_be_visible()
    # A second DBMS/Keys/Easy question, true/false this time.
    add.locator("[name=question_type]").select_option("true_false")
    add.locator("[name=text]").fill("A candidate key can be chosen as the primary key.")
    add.locator("[name=tf][value=True]").check()
    add.locator("[name=subject]").fill("DBMS")
    add.locator("[name=topic]").fill("Keys")
    add.locator("button:has-text('Submit question')").click()
    expect(add.locator(".alert.success")).to_have_text("Question added to the bank")
    # Keyword search (IT_09, step 1).
    page.fill("form.search [name=q]", "primary key")
    page.click("form.search button:has-text('Search')")
    expect(page.locator("li.question")).to_have_count(2)

    # ---- Create Quiz (IT_10, ST_10) and publish it to a channel (ST_09) ----
    page.click("#nav >> text=Quizzes")
    create = page.locator("form.create-quiz")
    create.locator("[name=title]").fill("DBMS Quick Quiz")
    create.locator("[name=subject]").fill("DBMS")
    create.locator("[name=topic]").fill("Keys")
    create.locator("[name=difficulty]").select_option("easy")
    create.locator("[name=question_count]").fill("5")
    create.locator("[name=duration_minutes]").fill("1")
    create.locator("button:has-text('Generate quiz')").click()
    expect(create.locator(".alert.error")).to_be_visible()  # only 2 match: say so
    create.locator("[name=question_count]").fill("2")
    create.locator("button:has-text('Generate quiz')").click()
    expect(create.locator(".preview-questions li")).to_have_count(2)
    expect(create.locator(".preview-questions")).to_contain_text("What is a primary key?")
    expect(create.locator(".quiz-preview h3")).to_have_text("2 questions · 1 minutes")
    create.locator("button.save-quiz").click()
    expect(create.locator(".alert.success")).to_contain_text('Saved "DBMS Quick Quiz" as a draft')
    draft = page.locator("li.my-quiz", has_text="DBMS Quick Quiz")
    expect(draft.locator(".badge.draft")).to_be_visible()
    draft.locator("[name=publish_to]").select_option(label="DBMS study group (Normal)")
    draft.locator("button:has-text('Publish')").click()
    expect(draft.locator(".alert.success")).to_have_text(
        'Published "DBMS Quick Quiz" to DBMS study group'
    )

    # ---- A member attempts it: unanswered warning (UT_12), score at once (UT_11) ----
    page.click("#logout")
    log_in("student@example.com")
    page.click("text=DBMS study group")
    page.click(".channel-links >> text=Quizzes")
    quiz_row = page.locator(".quiz-list li.quiz", has_text="DBMS Quick Quiz")
    expect(quiz_row).to_be_visible()
    quiz_row.locator("text=Start quiz").click()
    expect(page.get_by_role("heading", name="DBMS Quick Quiz")).to_be_visible()
    expect(page.locator(".timer")).to_contain_text("Time left 00:")
    mcq = page.locator("fieldset.attempt-question", has_text="What is a primary key?")
    tf = page.locator("fieldset.attempt-question", has_text="candidate key")
    mcq.locator("label.choice", has_text="A unique row identifier").click()
    expect(page.locator(".unanswered")).to_have_text("1 question is unanswered")
    page.click("button:has-text('Submit quiz')")
    expect(page.locator(".confirm-submit")).to_contain_text("1 question is unanswered")
    page.click("button:has-text('Keep answering')")
    tf.locator("label.choice", has_text="True").click()
    expect(page.locator(".unanswered")).to_be_hidden()
    page.click("button:has-text('Submit quiz')")
    expect(page.get_by_role("heading", name="Result: DBMS Quick Quiz")).to_be_visible()
    expect(page.locator(".score strong")).to_have_text("2/2 (100%)")
    expect(page.locator(".tile-correct")).to_contain_text("2")

    # ---- Timer runs out: saved answers are auto-submitted (ST_11) ----
    page.click("#nav >> text=Quizzes")
    page.locator("[name=scope]").select_option(label="DBMS study group (Normal)")
    page.locator(".quiz-list li.quiz", has_text="DBMS Quick Quiz").locator(
        "text=Start quiz"
    ).click()
    mcq.locator("label.choice", has_text="A unique row identifier").click()
    page.wait_for_timeout(1200)  # the answer autosaves
    with Session() as db:
        attempt_id = db.scalar(
            select(QuizAttempt.id).where(QuizAttempt.status == AttemptStatus.IN_PROGRESS)
        )
        db.execute(
            update(QuizAttempt)
            .where(QuizAttempt.id == attempt_id)
            .values(expires_at=utcnow() + timedelta(seconds=3))
        )
        db.commit()
    page.reload()  # resumes with three seconds left, then the clock reaches zero
    expect(page.get_by_role("heading", name="Result: DBMS Quick Quiz")).to_be_visible(timeout=20000)
    expect(page.locator(".auto-submitted")).to_be_visible()
    expect(page.locator(".score strong")).to_have_text("1/2 (50%)")
    expect(page.locator(".tile-unattempted")).to_contain_text("1")

    # ---- My Quiz History lists every attempt (IT_11) ----
    page.click("#nav >> text=Quiz History")
    expect(page.get_by_role("heading", name="My Quiz History")).to_be_visible()
    rows = page.locator("table.history tbody tr")
    expect(rows).to_have_count(3)  # the seeded attempt plus the two above
    expect(rows.first).to_contain_text("1/2 (50%)")
    expect(rows.first).to_contain_text("auto-submitted")
    expect(rows.nth(1)).to_contain_text("2/2 (100%)")
    rows.nth(1).locator("text=View").click()
    expect(page.locator(".score strong")).to_have_text("2/2 (100%)")
    expect(page.locator(".answers")).to_contain_text("Correct answer: A unique row identifier")

    # ---- Rate a peer's question 4 stars and report another (IT_09) ----
    page.click("#nav >> text=Questions")
    rated = page.locator("li.question", has_text="What is a primary key?")
    rated.locator("[name=rating]").select_option("4")
    rated.locator("button:has-text('Rate')").click()
    expect(rated.locator(".alert.success")).to_contain_text("Rating saved")
    other = page.locator("li.question", has_text="candidate key")
    other.locator("[name=reason]").select_option("incorrect")
    other.locator("button:has-text('Report')").click()
    expect(other.locator(".alert.success")).to_have_text("Report submitted for moderation")

    # Phone width: the screens fit without horizontal scrolling.
    page.set_viewport_size({"width": 375, "height": 800})
    for route in ("#/materials", "#/questions", "#/quizzes", "#/history"):
        page.goto(base + "/app/" + route)
        expect(page.locator("main h1")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth") <= 375, route

    assert page_errors == []
