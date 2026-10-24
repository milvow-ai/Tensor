"""Tenancy models (F1A). They mirror the ``tenants``, ``users`` and ``memberships`` tables."""

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

# Milvow is tenant #1: the id every row created before tenancy already carries.
MILVOW_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class Tenant(BaseModel):
    id: UUID
    slug: str
    name: str
    cell_id: str = "cell-1"
    plan: str = "beta"
    status: Literal["active", "suspended", "deleted"] = "active"
    created_at: datetime
    updated_at: datetime


class User(BaseModel):
    id: UUID
    email: str
    display_name: str | None = None
    auth_subject: str | None = None
    locale: str = "en"
    timezone: str = "UTC"
    created_at: datetime
    last_seen_at: datetime | None = None


class Membership(BaseModel):
    tenant_id: UUID
    user_id: UUID
    role: Role
    created_at: datetime


class TenantContext(BaseModel):
    """Who is acting, for which tenant. Passed explicitly down every data path."""

    model_config = ConfigDict(frozen=True)

    tenant_id: UUID
    user_id: UUID | None
    roles: frozenset[Role]
    cell_id: str
