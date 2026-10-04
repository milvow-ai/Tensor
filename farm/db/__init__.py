"""Database package for Harness Farm.

* ``farm.db.pool``  - ``get_db_url()``, ``open_pool()``, ``close_pool()``, ``DbPool``.
* ``farm.db.local`` - embedded pgserver (local mode); imported lazily, not re-exported here.
* Alembic: configuration ``farm/db/alembic.ini`` (kept next to the package, loaded by ``farm db migrate``
  and the test fixtures), revisions in ``farm/db/migrations/versions`` (raw SQL via ``op.execute``).
"""

from farm.db.pool import DbPool, close_pool, get_db_url, is_local_db, is_local_url, open_pool

__all__ = ["DbPool", "close_pool", "get_db_url", "is_local_db", "is_local_url", "open_pool"]
