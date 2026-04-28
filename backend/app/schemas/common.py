"""Common schemas shared across multiple modules.

Provides pagination, date-range filtering, and sorting primitives
used by all list endpoints.
"""

from datetime import date, datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    """Generic paginated response wrapper.

    Attributes:
        items: List of result items for the current page.
        total: Total number of items matching the query.
        page: Current page number (1-indexed).
        per_page: Number of items per page.
        pages: Total number of pages.
    """

    items: list[T]
    total: int = Field(..., ge=0, description="Total matching items")
    page: int = Field(..., ge=1, description="Current page number")
    per_page: int = Field(..., ge=1, le=100, description="Items per page")
    pages: int = Field(..., ge=0, description="Total number of pages")


class DateRangeParams(BaseModel):
    """Date range filtering parameters.

    Attributes:
        start_date: Inclusive start date for filtering.
        end_date: Inclusive end date for filtering.
    """

    start_date: date | None = Field(None, description="Start date (inclusive)")
    end_date: date | None = Field(None, description="End date (inclusive)")


class SortParams(BaseModel):
    """Sorting parameters for list endpoints.

    Attributes:
        sort: Column name to sort by.
        order: Sort direction, ascending or descending.
    """

    sort: str = Field("created_at", description="Column to sort by")
    order: str = Field("desc", pattern="^(asc|desc)$", description="Sort direction")


class ErrorResponse(BaseModel):
    """Standard error response body.

    Attributes:
        detail: Human-readable error description.
    """

    detail: str
