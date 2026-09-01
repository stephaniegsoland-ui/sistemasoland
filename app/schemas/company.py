import uuid
from datetime import date, datetime
from typing import Optional, List

from pydantic import BaseModel, Field


class CompanyBase(BaseModel):
    name: str
    rif: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    contact_name: Optional[str] = None
    status: str = "activo"
    default_retention_percent: float = 0.0


class CompanyCreate(CompanyBase):
    pass


class CompanyUpdate(BaseModel):
    name: Optional[str] = None
    rif: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    contact_name: Optional[str] = None
    status: Optional[str] = None
    default_retention_percent: Optional[float] = None


class CompanyRead(CompanyBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CompanyRetentionBase(BaseModel):
    company_id: uuid.UUID
    description: str
    amount: float = 0.0
    percent: float = 0.0
    status: str = "pendiente"
    due_date: Optional[date] = None


class CompanyRetentionCreate(CompanyRetentionBase):
    pass


class CompanyRetentionUpdate(BaseModel):
    description: Optional[str] = None
    amount: Optional[float] = None
    percent: Optional[float] = None
    status: Optional[str] = None
    due_date: Optional[date] = None


class CompanyRetentionRead(CompanyRetentionBase):
    id: uuid.UUID
    created_at: datetime

    class Config:
        from_attributes = True


class InvoiceRetentionBase(BaseModel):
    company_id: Optional[uuid.UUID] = None
    supplier_name: Optional[str] = None
    supplier_address: Optional[str] = None
    rif: Optional[str] = None
    total_amount: float = 0.0
    taxable_base: float = 0.0
    iva_amount: float = 0.0
    retention_amount: float = 0.0
    retention_percent: float = 0.0
    retention_status: str = "pending"
    collected: bool = False
    image_url: Optional[str] = None


class InvoiceRetentionCreate(InvoiceRetentionBase):
    pass


class InvoiceRetentionUpdate(BaseModel):
    company_id: Optional[uuid.UUID] = None
    supplier_name: Optional[str] = None
    supplier_address: Optional[str] = None
    rif: Optional[str] = None
    total_amount: Optional[float] = None
    taxable_base: Optional[float] = None
    iva_amount: Optional[float] = None
    retention_amount: Optional[float] = None
    retention_percent: Optional[float] = None
    retention_status: Optional[str] = None
    collected: Optional[bool] = None
    image_url: Optional[str] = None


class InvoiceRetentionRead(InvoiceRetentionBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CompanyWithRetentions(CompanyRead):
    retentions: List[CompanyRetentionRead] = Field(default_factory=list)
    retention_count: int = 0
