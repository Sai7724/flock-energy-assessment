"""Pydantic request/response models for the Urja Meter Ops API.

These schemas define the clean, typed JSON contract exposed to downstream
clients. Field names use snake_case to match common JSON API conventions.

Fields are intentionally permissive (``Optional`` with defaults) because the
legacy portal may omit data inconsistently. The client adapter is responsible
for normalising raw portal values into these shapes; Pydantic then validates
and serialises them.

RECON TODO: After inspecting the portal, tighten required vs optional fields
and add/remove fields to match what the portal actually returns.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# --- Auth ---------------------------------------------------------------------


class LoginRequest(BaseModel):
    """Request body for POST /api/v1/auth/login."""

    username: str = Field(..., min_length=1, description="Portal operator username.")
    password: str = Field(..., min_length=1, description="Portal operator password.")


class LoginResponse(BaseModel):
    """Response for POST /api/v1/auth/login."""

    success: bool = Field(..., description="Whether authentication succeeded.")
    message: str = Field(..., description="Human-readable status message.")
    session_active: bool = Field(
        ..., description="Whether a usable session is now established."
    )


# --- Meters -------------------------------------------------------------------


class Meter(BaseModel):
    """A smart meter summary as returned by the meters listing."""

    meter_id: str = Field(..., description="Unique meter identifier.")
    serial_number: Optional[str] = Field(
        None, description="Hardware serial number, if available."
    )
    status: str = Field(
        "UNKNOWN", description="Operational status, e.g. ACTIVE, INACTIVE, FAULT."
    )
    location: Optional[str] = Field(
        None, description="Install location / site name, if available."
    )


class MeterDetail(Meter):
    """Extended meter information returned for a single meter."""

    install_date: Optional[datetime] = Field(
        None, description="Meter installation timestamp, if available."
    )
    last_reading: Optional[float] = Field(
        None, description="Most recent meter reading value (kWh), if available."
    )
    last_reading_at: Optional[datetime] = Field(
        None, description="Timestamp of the most recent reading, if available."
    )
    firmware_version: Optional[str] = Field(
        None, description="Meter firmware version, if available."
    )
    customer: Optional[str] = Field(
        None, description="Associated customer / account name, if available."
    )


# --- Consumption --------------------------------------------------------------


class ConsumptionReading(BaseModel):
    """A single consumption measurement."""

    timestamp: datetime = Field(..., description="When the reading was taken (ISO 8601).")
    kwh: float = Field(..., description="Energy consumed in kilowatt-hours.")
    unit: str = Field("kWh", description="Unit of the reading value.")


class ConsumptionHistory(BaseModel):
    """Consumption history for a single meter."""

    meter_id: str = Field(..., description="The meter these readings belong to.")
    readings: List[ConsumptionReading] = Field(
        default_factory=list, description="Chronological consumption readings."
    )


# --- Hierarchy (optional extension) ------------------------------------------


class HierarchyNode(BaseModel):
    """A node in the meter network hierarchy tree."""

    node_id: str = Field(..., description="Unique node identifier.")
    name: str = Field(..., description="Display name of the node.")
    node_type: str = Field(
        ..., description="Node kind, e.g. region, site, feeder, meter."
    )
    children: List["HierarchyNode"] = Field(
        default_factory=list, description="Child nodes in the hierarchy."
    )


HierarchyNode.model_rebuild()


# --- Error envelope -----------------------------------------------------------


class ErrorDetail(BaseModel):
    """Standard error response body."""

    detail: str = Field(..., description="Human-readable error description.")
