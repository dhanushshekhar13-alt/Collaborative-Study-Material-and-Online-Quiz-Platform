"""Import every module's ORM models so ``Base.metadata`` is complete.

Alembic and the seed script import this module; each feature module registers its
models here.
"""

from app.modules.accounts import models as accounts  # noqa: F401
from app.modules.channels import models as channels  # noqa: F401
from app.modules.chat import models as chat  # noqa: F401
from app.modules.llm import models as llm  # noqa: F401
from app.modules.materials import models as materials  # noqa: F401
from app.modules.moderation import models as moderation  # noqa: F401
from app.modules.notifications import models as notifications  # noqa: F401
from app.modules.questions import models as questions  # noqa: F401
from app.modules.quizzes import models as quizzes  # noqa: F401
from app.modules.reputation import models as reputation  # noqa: F401
