"""Settings schemas for organization-level configuration.

Provides request/response models for reading and updating
organization settings stored in the JSONB column.
"""

from pydantic import BaseModel, Field


class OrgSettingsResponse(BaseModel):
    """Organization settings response.

    Attributes:
        settings: The full settings dictionary from the organization.
    """

    settings: dict = Field(default_factory=dict, description="Organization settings")


class OrgSettingsUpdateRequest(BaseModel):
    """Request to update organization settings.

    Attributes:
        settings: New settings dictionary to merge/replace.
    """

    settings: dict = Field(..., description="Settings object to save")
