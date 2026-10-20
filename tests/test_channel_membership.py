"""S02.2 - channel membership and member management (issue #21, FR-02).

Test Plan cases are named after their IDs: IT_03, IT_04, ST_03. The member list is the
channel-restricted content available today; later stories (materials, chat) gate
their content on the same ``channels.service.is_member`` check.
"""

import pytest
from fastapi.testclient import TestClient

from app.modules.accounts import service as accounts
from app.modules.accounts.models import Role
from app.modules.channels import service as channels
from app.modules.channels.models import ChannelType, MemberRole

API = "/api/v1/channels"
PASSWORD = "Pass@1234"


def make_user(db, username: str, role: Role = Role.STUDENT) -> int:
    user = accounts.create_user(
        db,
        email=f"{username}@test.com",
        display_name=username.title(),
        password=PASSWORD,
        role=role,
        username=username,
    )
    db.commit()
    return user.id


def auth(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/accounts/login", json={"username": username, "password": PASSWORD}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture()
def users(client, db) -> dict[str, int]:
    return {
        name: make_user(db, name)
        for name in ("creator_k", "aditi_r", "rahul_m", "mod_s", "outsider_p")
    }


@pytest.fixture()
def dbms(db, users) -> int:
    """'DBMS Study Group', created by creator_k, with mod_s as a moderator."""
    channel = channels.create_channel(
        db,
        name="DBMS Study Group",
        subject="Database Management Systems",
        created_by=users["creator_k"],
    )
    channels.add_member(db, channel.id, users["mod_s"], MemberRole.MODERATOR)
    db.commit()
    return channel.id


def join(client, channel_id: int, headers):
    return client.post(f"{API}/{channel_id}/join", headers=headers)


def member_names(client, channel_id: int, headers) -> list[str]:
    response = client.get(f"{API}/{channel_id}/members", headers=headers)
    assert response.status_code == 200, response.text
    return [m["username"] for m in response.json()]


def my_channel_ids(client, headers) -> list[int]:
    return [c["id"] for c in client.get(f"{API}/mine", headers=headers).json()]


# --------------------------------------------------------------------------- #
# IT_03 - search for a channel and join it
# --------------------------------------------------------------------------- #


def test_IT_03_search_and_join_adds_the_user_at_once(client, users, dbms):
    aditi = auth(client, "aditi_r")

    results = client.get(API, params={"q": "DBMS"}, headers=aditi).json()
    assert [c["name"] for c in results] == ["DBMS Study Group"]
    assert results[0]["my_role"] is None

    response = join(client, dbms, aditi)
    assert response.status_code == 200
    assert response.json()["my_role"] == "member"
    assert response.json()["member_count"] == 3

    assert "aditi_r" in member_names(client, dbms, aditi)
    assert client.get(f"{API}/{dbms}", headers=aditi).json()["my_role"] == "member"


def test_keyword_search_returns_only_matching_channels(client, db, users, dbms):
    channels.create_channel(db, name="Operating Systems", subject="OS", created_by=users["mod_s"])
    db.commit()
    aditi = auth(client, "aditi_r")

    names = [c["name"] for c in client.get(API, params={"q": "dbms"}, headers=aditi).json()]
    assert names == ["DBMS Study Group"]


def test_joining_grants_access_to_channel_content(client, users, dbms):
    aditi = auth(client, "aditi_r")
    assert client.get(f"{API}/{dbms}/members", headers=aditi).status_code == 403

    join(client, dbms, aditi)

    assert client.get(f"{API}/{dbms}/members", headers=aditi).status_code == 200


def test_a_joined_channel_appears_under_my_channels(client, users, dbms):
    aditi = auth(client, "aditi_r")
    assert my_channel_ids(client, aditi) == []

    join(client, dbms, aditi)

    mine = client.get(f"{API}/mine", headers=aditi).json()
    assert [(c["id"], c["my_role"]) for c in mine] == [(dbms, "member")]


def test_joining_twice_is_refused(client, users, dbms):
    aditi = auth(client, "aditi_r")
    join(client, dbms, aditi)

    response = join(client, dbms, aditi)

    assert response.status_code == 409
    assert response.json()["detail"] == "You are already a member of this channel"


def test_a_pending_authorized_channel_cannot_be_joined(client, db, users):
    faculty = make_user(db, "faculty_a", Role.AUTHORIZED_USER)
    pending = channels.create_channel(
        db,
        name="Official DBMS",
        subject="DBMS",
        created_by=faculty,
        channel_type=ChannelType.AUTHORIZED,
    )
    db.commit()

    response = join(client, pending.id, auth(client, "aditi_r"))

    assert response.status_code == 404


def test_membership_endpoints_require_sign_in(client, dbms):
    assert client.post(f"{API}/{dbms}/join").status_code == 401
    assert client.post(f"{API}/{dbms}/leave").status_code == 401
    assert client.get(f"{API}/mine").status_code == 401
    assert client.get(f"{API}/{dbms}/members").status_code == 401


# --------------------------------------------------------------------------- #
# IT_04 - leave a channel
# --------------------------------------------------------------------------- #


def test_IT_04_leaving_removes_the_user_and_the_channel_from_my_channels(client, users, dbms):
    aditi = auth(client, "aditi_r")
    join(client, dbms, aditi)

    response = client.post(f"{API}/{dbms}/leave", headers=aditi)

    assert response.status_code == 204
    assert "aditi_r" not in member_names(client, dbms, auth(client, "creator_k"))
    assert dbms not in my_channel_ids(client, aditi)


def test_after_leaving_channel_content_is_closed(client, users, dbms):
    aditi = auth(client, "aditi_r")
    join(client, dbms, aditi)
    client.post(f"{API}/{dbms}/leave", headers=aditi)

    response = client.get(f"{API}/{dbms}/members", headers=aditi)

    assert response.status_code == 403
    assert response.json()["detail"] == "Join this channel to view its content"


def test_leaving_a_channel_you_are_not_in_is_refused(client, users, dbms):
    response = client.post(f"{API}/{dbms}/leave", headers=auth(client, "aditi_r"))

    assert response.status_code == 409
    assert response.json()["detail"] == "You are not a member of this channel"


def test_the_creator_cannot_leave_their_own_channel(client, users, dbms):
    response = client.post(f"{API}/{dbms}/leave", headers=auth(client, "creator_k"))

    assert response.status_code == 409
    assert response.json()["detail"] == "The channel creator cannot leave their own channel"


# --------------------------------------------------------------------------- #
# ST_03 - creator or moderator removes a member
# --------------------------------------------------------------------------- #


def test_ST_03_creator_removes_a_member_who_loses_access(client, users, dbms):
    aditi = auth(client, "aditi_r")
    join(client, dbms, aditi)
    creator = auth(client, "creator_k")
    assert "aditi_r" in member_names(client, dbms, creator)

    response = client.delete(
        f"{API}/{dbms}/members/{users['aditi_r']}", params={"confirm": "true"}, headers=creator
    )

    assert response.status_code == 204
    assert "aditi_r" not in member_names(client, dbms, creator)
    assert client.get(f"{API}/{dbms}/members", headers=aditi).status_code == 403
    assert dbms not in my_channel_ids(client, aditi)


def test_a_moderator_can_remove_a_member(client, users, dbms):
    join(client, dbms, auth(client, "aditi_r"))
    moderator = auth(client, "mod_s")

    response = client.delete(
        f"{API}/{dbms}/members/{users['aditi_r']}", params={"confirm": "true"}, headers=moderator
    )

    assert response.status_code == 204
    assert "aditi_r" not in member_names(client, dbms, moderator)


def test_removal_needs_confirmation(client, users, dbms):
    join(client, dbms, auth(client, "aditi_r"))
    creator = auth(client, "creator_k")

    response = client.delete(f"{API}/{dbms}/members/{users['aditi_r']}", headers=creator)

    assert response.status_code == 400
    assert response.json()["detail"] == "Confirm removal of this member"
    assert "aditi_r" in member_names(client, dbms, creator)


def test_a_regular_member_cannot_remove_anyone(client, users, dbms):
    join(client, dbms, auth(client, "aditi_r"))
    rahul = auth(client, "rahul_m")
    join(client, dbms, rahul)

    response = client.delete(
        f"{API}/{dbms}/members/{users['aditi_r']}", params={"confirm": "true"}, headers=rahul
    )

    assert response.status_code == 403
    assert response.json()["detail"] == (
        "Only the channel creator or a moderator can remove members"
    )
    assert "aditi_r" in member_names(client, dbms, rahul)


def test_a_non_member_cannot_remove_anyone(client, users, dbms):
    join(client, dbms, auth(client, "aditi_r"))

    response = client.delete(
        f"{API}/{dbms}/members/{users['aditi_r']}",
        params={"confirm": "true"},
        headers=auth(client, "outsider_p"),
    )

    assert response.status_code == 403


def test_a_moderator_cannot_remove_the_creator(client, users, dbms):
    response = client.delete(
        f"{API}/{dbms}/members/{users['creator_k']}",
        params={"confirm": "true"},
        headers=auth(client, "mod_s"),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "You cannot remove a member with this role"


def test_the_creator_can_remove_a_moderator(client, users, dbms):
    creator = auth(client, "creator_k")

    response = client.delete(
        f"{API}/{dbms}/members/{users['mod_s']}", params={"confirm": "true"}, headers=creator
    )

    assert response.status_code == 204
    assert "mod_s" not in member_names(client, dbms, creator)


def test_removing_someone_who_is_not_a_member_is_not_found(client, users, dbms):
    response = client.delete(
        f"{API}/{dbms}/members/{users['outsider_p']}",
        params={"confirm": "true"},
        headers=auth(client, "creator_k"),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "This user is not a member of the channel"


def test_the_creator_cannot_remove_themselves(client, users, dbms):
    response = client.delete(
        f"{API}/{dbms}/members/{users['creator_k']}",
        params={"confirm": "true"},
        headers=auth(client, "creator_k"),
    )

    assert response.status_code == 400


def test_member_list_shows_owner_then_moderators_then_members(client, users, dbms):
    aditi = auth(client, "aditi_r")
    join(client, dbms, aditi)

    members = client.get(f"{API}/{dbms}/members", headers=aditi).json()

    assert [(m["username"], m["role"]) for m in members] == [
        ("creator_k", "owner"),
        ("mod_s", "moderator"),
        ("aditi_r", "member"),
    ]
    assert members[0]["display_name"] == "Creator_K"


def test_a_removed_member_can_rejoin(client, users, dbms):
    aditi = auth(client, "aditi_r")
    join(client, dbms, aditi)
    client.delete(
        f"{API}/{dbms}/members/{users['aditi_r']}",
        params={"confirm": "true"},
        headers=auth(client, "creator_k"),
    )

    assert join(client, dbms, aditi).status_code == 200


def test_service_membership_helpers(db, users, dbms):
    assert channels.is_member(db, dbms, users["mod_s"])
    assert channels.remove_member(db, dbms, users["mod_s"]) is True
    assert not channels.is_member(db, dbms, users["mod_s"])
    assert channels.remove_member(db, dbms, users["mod_s"]) is False
    mine = channels.channels_for_member(db, users["creator_k"])
    assert [(c.id, role) for c, role in mine] == [(dbms, MemberRole.OWNER)]
