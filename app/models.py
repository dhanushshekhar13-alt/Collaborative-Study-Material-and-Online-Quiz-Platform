"""Import every module's ORM models so ``Base.metadata`` is complete.

Alembic and the seed script import this module; each feature module registers its
models here when it is added.
"""
