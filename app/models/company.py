import uuid
from datetime import date, datetime
from typing import List, Optional

from sqlmodel import Field, Relationship, SQLModel


class Company(SQLModel, table=True):
    __tablename__ = "companies"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str = Field(index=True, nullable=False)
    rif: Optional[str] = Field(default=None, index=True, nullable=True)
    address: Optional[str] = Field(default=None, nullable=True)
    phone: Optional[str] = Field(default=None, nullable=True)
    email: Optional[str] = Field(default=None, nullable=True)
    contact_name: Optional[str] = Field(default=None, nullable=True)
    status: str = Field(default="activo", nullable=False)
    default_retention_percent: float = Field(default=0.0, nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    retentions: List["CompanyRetention"] = Relationship(back_populates="company")


class CompanyRetention(SQLModel, table=True):
    __tablename__ = "company_retentions"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    company_id: uuid.UUID = Field(foreign_key="companies.id", index=True, nullable=False)
    description: str = Field(nullable=False)
    amount: float = Field(default=0.0, nullable=False)
    percent: float = Field(default=0.0, nullable=False)
    status: str = Field(default="pendiente", nullable=False)
    due_date: Optional[date] = Field(default=None, nullable=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    company: Optional[Company] = Relationship(back_populates="retentions")


class InvoiceRetention(SQLModel, table=True):
    __tablename__ = "invoice_retentions"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    company_id: Optional[uuid.UUID] = Field(default=None, foreign_key="companies.id", index=True, nullable=True)
    supplier_name: Optional[str] = Field(default=None, nullable=True)
    supplier_address: Optional[str] = Field(default=None, nullable=True)
    rif: Optional[str] = Field(default=None, index=True, nullable=True)
    total_amount: float = Field(default=0.0, nullable=False)
    taxable_base: float = Field(default=0.0, nullable=False)
    iva_amount: float = Field(default=0.0, nullable=False)
    retention_amount: float = Field(default=0.0, nullable=False)
    retention_percent: float = Field(default=0.0, nullable=False)
    retention_status: str = Field(default="pending", nullable=False)
    collected: bool = Field(default=False, nullable=False)
    image_url: Optional[str] = Field(default=None, nullable=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    company: Optional[Company] = Relationship()
