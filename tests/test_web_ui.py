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

SCRIPTS = ["api.js", "app.js"]


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
    source = (STATIC_DIR / "app.js").read_text()
    pattern = r"API\.(get|post|patch|del)\(\s*`?\"?(/[^`\"?$]*(?:\$\{[^}]+\}[^`\"?$]*)*)"
    for method, path in re.findall(pattern, source):
        template = re.sub(r"\$\{[^}]+\}", "{id}", path)
        template = re.sub(r"(?<!/)\{id\}$", "", template)  # a trailing ?query, not an id
        calls.add(({"del": "delete"}.get(method, method), template))
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
def live_server(tmp_path):
    """The real app on a free port, backed by a seeded throwaway SQLite file."""
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
    yield f"http://127.0.0.1:{port}"
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
