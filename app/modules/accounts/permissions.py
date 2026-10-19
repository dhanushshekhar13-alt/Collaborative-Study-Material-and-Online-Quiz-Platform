"""What each role is allowed to do.

Authorization is decided here and enforced by the route guards in ``auth.py``. The UI
may read a user's capabilities to decide what to render, but hiding a control is never
what makes an action safe: every protected route declares the capability it needs, so
the check happens on the server whatever the client does.
"""

import enum

from app.modules.accounts.models import Role


class Capability(enum.StrEnum):
    """A single permitted action, named ``<area>:<verb>``."""

    MATERIAL_UPLOAD = "material:upload"
    MATERIAL_RATE = "material:rate"
    CHANNEL_CREATE_NORMAL = "channel:create_normal"
    CHANNEL_CREATE_AUTHORIZED = "channel:create_authorized"
    QUESTION_CONTRIBUTE = "question:contribute"
    QUIZ_ATTEMPT = "quiz:attempt"
    QUIZ_PUBLISH = "quiz:publish"
    OFFICIAL_CONTENT_MANAGE = "official_content:manage"
    REPORT_SUBMIT = "report:submit"
    REPORT_RESOLVE = "report:resolve"
    USER_MANAGE = "user:manage"


# Every signed-in user is a learner first, so the student set is the base that the
# other roles extend.
_STUDENT: frozenset[Capability] = frozenset(
    {
        Capability.MATERIAL_UPLOAD,
        Capability.MATERIAL_RATE,
        Capability.CHANNEL_CREATE_NORMAL,
        Capability.QUESTION_CONTRIBUTE,
        Capability.QUIZ_ATTEMPT,
        Capability.QUIZ_PUBLISH,
        Capability.REPORT_SUBMIT,
    }
)

_AUTHORIZED_USER: frozenset[Capability] = _STUDENT | {
    Capability.CHANNEL_CREATE_AUTHORIZED,
    Capability.OFFICIAL_CONTENT_MANAGE,
}

_ADMIN: frozenset[Capability] = _AUTHORIZED_USER | {
    Capability.REPORT_RESOLVE,
    Capability.USER_MANAGE,
}

_BY_ROLE: dict[Role, frozenset[Capability]] = {
    Role.STUDENT: _STUDENT,
    Role.AUTHORIZED_USER: _AUTHORIZED_USER,
    Role.ADMIN: _ADMIN,
}


def capabilities_for(role: Role) -> frozenset[Capability]:
    """Return everything ``role`` is permitted to do."""
    return _BY_ROLE[role]


def role_has(role: Role, capability: Capability) -> bool:
    return capability in _BY_ROLE[role]
