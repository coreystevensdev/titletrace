"""LangGraph state schema and all shared Pydantic models for TitleTrace.

The state flows through a single LangGraph StateGraph. All node outputs are
typed fields on TraceState so the graph compiler can validate edges at build
time. Pydantic models represent API response shapes; TypedDict represents the
accumulated graph state.
"""

from __future__ import annotations

from typing import Any
from typing_extensions import TypedDict

from pydantic import BaseModel, Field


class ParcelResult(BaseModel):
    parcel_id: str
    address: str
    city: str
    state: str
    zip_code: str
    owner_name: str | None = None
    lot_size_sqft: float | None = None
    year_built: int | None = None
    land_use: str | None = None
    # ATTOM's /property/detail response includes these under a `location`
    # block (confirmed against ATTOM's public API docs). Philadelphia's OPA
    # dataset is assumed to expose "lat"/"lng" top-level fields -- this could
    # not be live-verified (data.phila.gov returned HTTP 403 to this
    # session's sandbox IP), so treat it as an educated guess, not a
    # confirmed fact, until a real OPA response is captured.
    latitude: float | None = None
    longitude: float | None = None
    source: str


class OwnerRecord(BaseModel):
    owner_name: str
    sale_date: str | None = None
    sale_price: float | None = None
    document_type: str | None = None
    source: str


class LienResult(BaseModel):
    lien_type: str
    amount: float | None = None
    recorded_date: str | None = None
    lienholder: str | None = None
    status: str
    source: str


class EncumbranceResult(BaseModel):
    encumbrance_type: str
    description: str | None = None
    recorded_date: str | None = None
    source: str


class ZoningResult(BaseModel):
    zoning_code: str
    zoning_description: str | None = None
    permit_count: int | None = None
    source: str


class TaxStatus(BaseModel):
    is_delinquent: bool
    balance_due: float | None = None
    last_paid_year: int | None = None
    source: str


class TaxClaimDetail(BaseModel):
    claim_year: int | None = None
    amount: float | None = None
    lienholder: str | None = None
    status: str | None = None
    source: str


class FloodZoneResult(BaseModel):
    zone_designation: str
    zone_description: str
    firm_panel: str | None = None
    effective_date: str | None = None
    source: str = "FEMA NFHL ArcGIS REST"


class LienholderDetail(BaseModel):
    lien_type: str
    lienholder_name: str | None = None
    lienholder_address: str | None = None
    ucc_filing_number: str | None = None
    source: str


class DataUnavailable(BaseModel):
    reason: str
    recommended_action: str | None = None


class TraceReport(BaseModel):
    """Synthesized output from the generate node. Always produced even when
    some data sources are unavailable; missing fields surface as DataUnavailable."""

    parcel_id: str | None = None
    address: str
    current_owner: str | None = None
    ownership_history_count: int = 0
    lien_count: int = 0
    lien_summary: str | None = None
    encumbrance_count: int = 0
    tax_delinquent: bool | None = None
    tax_balance_due: float | None = None
    zoning: str | None = None
    flood_zone: str | None = None
    estimated_title_premium_usd: float | None = None
    findings: list[str] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)
    confidence: str = "partial"


class TraceState(TypedDict):
    raw_address: str
    parcel_id: str | None
    state: str | None
    city: str | None
    parcel: ParcelResult | None
    ownership_history: list[OwnerRecord]
    liens: list[LienResult]
    encumbrances: list[EncumbranceResult]
    zoning: ZoningResult | None
    tax_status: TaxStatus | None
    flood_zone: FloodZoneResult | None
    lienholder_details: list[LienholderDetail]
    tax_claim_detail: TaxClaimDetail | None
    report: TraceReport | None
    error: str | None
