"""UI-3 - web UI for chat, AI help, analytics, notifications, moderation and admin (#64).

Contract tests that always run, plus Playwright walk-throughs of every acceptance
criterion against a live, seeded server (skipped where Chromium is not installed, as
in CI). The Test Plan steps for these flows are run through the pages: UT_19, UT_20,
IT_19, IT_20 (channel chat), UT_21, UT_22, IT_22 (direct messages), UT_17, UT_18,
IT_17 and the generated-question review (Ask AI), UT_23/UT_24 and the empty state
(Performance), UT_27, UT_28 (notifications), IT_25, IT_26 (moderation), and IT_27,
IT_28, ST_27 (admin).
"""

import httpx
import pytest

from app.core import config
from app.main import API_PREFIX
from app.web import STATIC_DIR
from tests.test_web_ui import _signed_in, browser, live_app  # noqa: F401  (fixtures)

PASSWORD = "Seed@1234"


def test_UI_3_pages_are_registered_and_linked():
    source = (STATIC_DIR / "community.js").read_text()
    for page in ("users", "messages", "ask", "notifications", "moderation", "admin"):
        assert f"pages.{page} = " in source, page
    html = (STATIC_DIR / "index.html").read_text()
    for href in ("#/messages", "#/ask", "#/notifications", "#/moderation", "#/admin"):
        assert f'href="{href}"' in html, href
    # Role pages are linked only for roles that can use them.
    assert 'data-cap="admin:dashboard"' in html
    assert html.index("community.js") < html.index("app.js")


def test_UI_3_blank_question_is_stopped_in_the_page_with_the_test_plan_wording():
    """UT_18: the page itself refuses a blank query, so nothing is sent."""
    source = (STATIC_DIR / "community.js").read_text()
    assert 'show(status, "Please enter a question", "error");' in source


def test_UI_3_send_is_disabled_while_the_message_box_is_blank():
    """UT_20: Send starts disabled and follows the box's content."""
    source = (STATIC_DIR / "community.js").read_text()
    assert 'h("button", { type: "submit", disabled: true }, buttonText)' in source
    assert "send.disabled = !text.value.trim();" in source


# --------------------------------------------------------------------------- #
# Browser walk-through
# --------------------------------------------------------------------------- #


@pytest.fixture()
def fake_llm(monkeypatch):
    """Ask AI and question generation use the offline, deterministic provider."""
    monkeypatch.setattr(config.settings, "llm_provider", "fake")


def _api(base: str, who: str) -> httpx.Client:
    return httpx.Client(base_url=f"{base}{API_PREFIX}", headers=_signed_in(base, who))


def test_UI_3_acceptance_criteria_in_the_browser(live_app, browser, fake_llm):  # noqa: F811
    from playwright.sync_api import expect

    base, _Session = live_app
    httpx.post(
        f"{base}{API_PREFIX}/accounts/register",
        json={
            "display_name": "Spam User",
            "username": "spam_user01",
            "email": "spam_user01@example.com",
            "password": PASSWORD,
        },
    ).raise_for_status()
    student_api = _api(base, "student@example.com")
    faculty_api = _api(base, "faculty@example.com")
    dbms = next(
        c for c in student_api.get("/channels/mine").json() if c["name"] == "DBMS study group"
    )

    errors: list[str] = []

    def new_page(who: str):
        page = browser.new_page(viewport={"width": 1200, "height": 900})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("dialog", lambda dialog: dialog.accept())
        page.goto(base + "/app/#/login")
        page.fill("[name=username]", who)
        page.fill("[name=password]", PASSWORD)
        page.click("button:has-text('Log in')")
        expect(page.get_by_role("heading", name="Channels", exact=True)).to_be_visible()
        return page

    student = new_page("student@example.com")
    faculty = new_page("faculty@example.com")
    outsider = new_page("spam_user01")

    # ---- IT_20: a non-member opening the channel chat is refused -------------
    outsider.goto(f"{base}/app/#/channels/{dbms['id']}")
    chat = outsider.locator("section.chat")
    expect(chat.locator(".alert.error")).to_have_text("Join the channel to view this chat")
    expect(chat.locator(".composer")).to_have_count(0)

    # ---- UT_20 / UT_19: blank Send is disabled; a message shows sender + time --
    student.goto(f"{base}/app/#/channels/{dbms['id']}")
    chat = student.locator("section.chat")
    send = chat.locator(".composer button:has-text('Send')")
    expect(send).to_be_disabled()
    chat.locator(".composer textarea").fill("   ")
    expect(send).to_be_disabled()
    chat.locator(".composer textarea").fill("Can someone share the ER diagram notes?")
    send.click()
    sent = chat.locator(".chat-messages > li.message", has_text="ER diagram notes")
    expect(sent.locator(".sender")).to_have_text("Sample Student")
    expect(sent.locator("time")).not_to_have_text("")
    expect(chat.locator(".composer textarea")).to_have_value("")

    # Visible to every member, with the same sender (UT_19).
    faculty.goto(f"{base}/app/#/channels/{dbms['id']}")
    other = faculty.locator(".chat-messages > li.message", has_text="ER diagram notes")
    expect(other.locator(".sender")).to_have_text("Sample Student")

    # ---- IT_19: a reply is nested under its parent ------------------------------
    other.locator("button.reply-toggle").click()
    other.locator(".thread textarea").fill("Yes, uploading now!")
    other.locator(".thread button:has-text('Reply')").click()
    reply = other.locator(".thread .replies li.reply", has_text="Yes, uploading now!")
    expect(reply.locator(".sender")).to_have_text("Sample Faculty")
    expect(other.locator("button.reply-toggle")).to_have_text("1 reply · Reply")

    # ---- UT_27: the bell shows the reply and opens it ------------------------
    student.goto(f"{base}/app/#/my-channels")
    expect(student.locator("#bell-count")).to_have_text("1")
    student.click("#bell")
    note = student.locator("li.notification", has_text="replied to your message")
    expect(note).to_have_class("notification unread clickable")
    note.click()
    expect(student).to_have_url(
        f"{base}/app/#/channels/{dbms['id']}?message={reply_id(student_api, dbms['id'])}"
    )
    expect(student.locator(".thread li.reply.highlight")).to_contain_text("Yes, uploading now!")
    expect(student.locator("#bell-count")).to_be_hidden()

    # ---- UT_28: switch a category off in Notification Settings ---------------
    student.click("#bell")
    box = student.locator(".notification-settings input[name=quiz_update]")
    expect(box).to_be_checked()
    box.uncheck()
    expect(student.locator(".notification-settings .alert.success")).to_contain_text("off")
    prefs = {p["kind"]: p["enabled"] for p in student_api.get("/notifications/preferences").json()}
    assert prefs["quiz_update"] is False and prefs["reply"] is True

    # ---- UT_21: profile -> Message -> send ---------------------------------
    student.goto(f"{base}/app/#/channels/{dbms['id']}")
    student.locator(".members a", has_text="Sample Faculty").click()
    expect(student.get_by_role("heading", name="Sample Faculty")).to_be_visible()
    student.click("button:has-text('Message')")
    expect(student.get_by_role("heading", name="Sample Faculty")).to_be_visible()
    student.locator(".composer textarea").fill("Hey, can you share your quiz notes?")
    student.click(".composer button:has-text('Send')")
    mine = student.locator(".chat-messages li.message.mine", has_text="share your quiz notes")
    expect(mine.locator(".sender")).to_have_text("Sample Student")

    faculty.click("#nav >> text=Messages")
    faculty.locator("li.conversation a", has_text="Sample Student").click()
    received = faculty.locator(".chat-messages li.message", has_text="share your quiz notes")
    expect(received.locator(".sender")).to_have_text("Sample Student")

    # ---- IT_22: report a message with a reason --------------------------------
    received.locator("button.report").click()
    received.locator(".report-form button:has-text('Submit report')").click()
    expect(received.locator(".report-form .alert.error")).to_have_text(
        "Select a reason for reporting this message"
    )
    received.locator(".report-form select").select_option("inappropriate")
    received.locator(".report-form button:has-text('Submit report')").click()
    expect(received.locator(".report-form .alert.success")).to_be_visible()

    # ---- UT_22: block; the blocked user can no longer send ----------------------
    faculty.click("button:has-text('Block User')")
    expect(faculty.locator(".blocked-indicator")).to_contain_text("You have blocked Sample Student")
    expect(faculty.locator(".composer")).to_be_hidden()
    expect(faculty.locator("button:has-text('Unblock User')")).to_be_visible()
    student.reload()
    expect(student.locator(".blocked-indicator")).to_have_text(
        "This conversation is blocked. Messages can't be sent."
    )
    expect(student.locator(".chat-head .badge.blocked")).to_have_text("Blocked")
    expect(student.locator(".composer")).to_be_hidden()

    # ---- UT_18 / UT_17 / IT_17: Ask AI ---------------------------------------
    asked: list[str] = []
    student.on("request", lambda r: asked.append(r.url) if r.url.endswith("/ai/ask") else None)
    student.click("#nav >> text=Ask AI")
    ask = student.locator("form.ask")
    ask.locator("button:has-text('Submit')").click()
    expect(ask.locator(".alert.error")).to_have_text("Please enter a question")
    assert asked == []  # nothing was sent
    ask.locator("[name=query]").fill("Explain B-Trees with an example")
    ask.locator("button:has-text('Submit')").click()
    answer = student.locator(".answer.explanation")
    expect(answer.locator(".explanation-text")).to_contain_text("B-Tree")
    expect(answer.locator(".examples li")).not_to_have_count(0)

    ask.locator("[name=query]").fill("Give me a quiz on Binary Search Trees")
    ask.locator("button:has-text('Submit')").click()
    offer = student.locator(".answer.quiz-offer")
    expect(offer).to_contain_text("Binary Search Trees")
    expect(offer.locator("a:has-text('Review the generated questions')")).to_be_visible()
    offer.locator("button:has-text('Start quiz')").click()
    expect(student.locator(".timer")).to_be_visible()
    expect(student.locator(".attempt-head h1")).to_contain_text("Binary Search Trees")

    # ---- Review screen: approve, edit and reject generated questions ------------
    student.click("#nav >> text=Ask AI")
    gen = student.locator("form.generate")
    gen.locator("[name=subject]").fill("DBMS")
    gen.locator("[name=topic]").fill("Indexing")
    gen.locator("button:has-text('Generate')").click()
    expect(student.get_by_role("heading", name="Review questions: DBMS › Indexing")).to_be_visible()
    items = student.locator(".review-item")
    expect(items).to_have_count(5)
    items.nth(0).locator("button:has-text('Approve')").click()
    expect(items.nth(0).locator(".badge.review-approved")).to_be_visible()
    items.nth(1).locator("button:has-text('Reject')").click()
    expect(items.nth(1).locator(".badge.review-rejected")).to_be_visible()
    items.nth(2).locator("button:has-text('Edit')").click()
    items.nth(2).locator(".edit-question [name=text]").fill(
        "Which index structure keeps keys sorted?"
    )
    items.nth(2).locator(".edit-question button:has-text('Save changes')").click()
    expect(items.nth(2).locator(".question-text")).to_contain_text(
        "Which index structure keeps keys sorted?"
    )
    for i in (2, 3, 4):
        items.nth(i).locator("button:has-text('Approve')").click()
        expect(items.nth(i).locator(".badge.review-approved")).to_be_visible()
    quiz_form = student.locator("form.add-to-quiz")
    quiz_form.locator("[name=title]").fill("Indexing practice")
    quiz_form.locator("button:has-text('Create quiz')").click()
    expect(quiz_form.locator(".alert.success")).to_be_visible()

    # ---- Performance: charts + Recommended for You; empty state invites a quiz --
    student.click("#nav >> text=Performance")
    expect(student.locator("figure.chart svg").first).to_be_visible()
    expect(student.locator(".recommended-preview h2")).to_have_text("Recommended for You")
    outsider.click("#nav >> text=Performance")
    expect(outsider.locator(".empty-state")).to_contain_text(
        "Attempt a quiz to see your performance here."
    )
    expect(outsider.locator(".recommended-preview")).to_be_visible()

    # ---- IT_25 / IT_26: moderation queue, remove content ---------------------------
    material = next(
        m
        for m in student_api.get("/materials").json()["items"]
        if m["title"] == "Normal forms cheat sheet"
    )
    faculty_api.post(
        f"/materials/{material['id']}/reports", json={"reason": "inappropriate"}
    ).raise_for_status()
    outsider.goto(f"{base}/app/#/moderation")
    expect(outsider.locator(".alert.error")).to_have_text("Only moderators can review reports")
    expect(outsider.locator("#nav a[href='#/moderation']")).to_be_hidden()
    admin = new_page("admin@example.com")
    admin.click("#nav >> text=Moderation")
    item = admin.locator("li.report-item", has_text="Normal forms cheat sheet")
    expect(item.locator(".reporters")).to_contain_text("Sample Faculty — Inappropriate content")
    item.locator("button:has-text('Remove Content')").click()
    expect(item.locator(".alert.success")).to_contain_text("Content removed · Status: resolved")

    # ---- IT_28: a student opening the admin URL sees "Unauthorized" -------------
    outsider.goto(f"{base}/app/#/admin")
    expect(outsider.locator(".alert.error")).to_have_text("Unauthorized")
    expect(outsider.locator(".tiles")).to_have_count(0)
    expect(outsider.locator("#nav a[href='#/admin']")).to_be_hidden()

    # ---- IT_27: suspend a user; ST_27: approve an Authorized Channel request ------
    faculty_api.post(
        "/channels", json={"name": "Official CS301", "channel_type": "authorized", "subject": "CS"}
    ).raise_for_status()
    admin.click("#nav >> text=Admin")
    admin.fill(".search [name=q]", "spam_user01")
    admin.click(".search button:has-text('Search')")
    row = admin.locator("li[data-user=spam_user01]")
    row.locator("button:has-text('Suspend')").click()
    expect(admin.locator("li[data-user=spam_user01] .badge")).to_have_text("Suspended")
    login = httpx.post(
        f"{base}{API_PREFIX}/accounts/login", json={"username": "spam_user01", "password": PASSWORD}
    )
    assert login.status_code in (401, 403)
    request = admin.locator("li[data-channel='Official CS301']")
    request.locator("button:has-text('Approve')").click()
    expect(admin.locator(".channel-requests").locator("..").locator(".alert.success")).to_have_text(
        '"Official CS301" is now approved'
    )
    names = [c["name"] for c in student_api.get("/channels", params={"type": "authorized"}).json()]
    assert "Official CS301" in names

    # ---- NFR-03: usable at phone width (no horizontal scroll at 375 px) -----------
    for page, route in [
        (student, f"#/channels/{dbms['id']}"),
        (student, "#/messages"),
        (student, "#/ask"),
        (student, "#/notifications"),
        (admin, "#/moderation"),
        (admin, "#/admin"),
    ]:
        page.set_viewport_size({"width": 375, "height": 800})
        page.goto(f"{base}/app/{route}")
        expect(page.locator("main h1").first).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth") <= 375, route

    assert errors == []


def reply_id(api: httpx.Client, channel_id: int) -> int:
    top = api.get(f"/channels/{channel_id}/messages").json()["items"][-1]
    return api.get(f"/channels/{channel_id}/messages/{top['id']}/replies").json()["items"][-1]["id"]
