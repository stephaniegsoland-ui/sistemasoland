import json
import os
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.auth import current_active_user
from app.core.db import get_async_session
from app.models.company import Company, CompanyRetention, InvoiceRetention
from app.models.chat import ChatMessage
from app.models.environment import EnvironmentalDocument, EnvironmentalDrill, EnvironmentalTalk
from app.models.inventory import ItemInventary
from app.models.user import User
from app.models.procura import Procura
from app.models.timesheet import Timesheet
from app.models.vehicle import Vehicle, VehicleInspection

router = APIRouter()

MODULE_CONTEXT = """
Sistema SOLAND: módulos principales y propósito.
- Stock e inventario: controla cantidades, categorías, faltantes y reposición.
- Personal: usuarios, perfiles, permisos, dotación y roles.
- Vehículos: flota, asignación, mantenimiento, inspecciones y seguimiento operativo.
- Seguridad EPP: inspecciones, permisos, riesgos y cumplimiento de seguridad.
- Ambiente: capacitaciones, simulacros, residuos y cumplimiento ambiental.
- Reportes: indicadores, KPI, desempeño y tendencia operativa.
- Procura: solicitudes, compras, proveedores y trazabilidad de materiales.
- Tiempo / Timesheet: registro de horas, turnos y actividades del personal.
- Chat interno: comunicación, alertas y coordinación entre usuarios.
- Administración: empresas, documentos, retenciones, peajes y control administrativo.
"""


class AssistantQuery(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)


async def _get_system_snapshot(session: AsyncSession) -> dict:
    companies = (await session.execute(select(Company))).scalars().all()
    vehicles = (await session.execute(select(Vehicle))).scalars().all()
    company_retentions = (await session.execute(select(CompanyRetention))).scalars().all()
    invoice_retentions = (await session.execute(select(InvoiceRetention))).scalars().all()
    users = (await session.execute(select(User))).scalars().all()
    inventory_items = (await session.execute(select(ItemInventary))).scalars().all()
    procurement = (await session.execute(select(Procura))).scalars().all()
    timesheets = (await session.execute(select(Timesheet))).scalars().all()
    inspections = (await session.execute(select(VehicleInspection))).scalars().all()
    environmental_talks = (await session.execute(select(EnvironmentalTalk))).scalars().all()
    environmental_drills = (await session.execute(select(EnvironmentalDrill))).scalars().all()
    environmental_documents = (await session.execute(select(EnvironmentalDocument))).scalars().all()
    chat_messages = (await session.execute(select(ChatMessage))).scalars().all()
    security_reports_count = 0
    safety_permits_count = 0
    try:
        security_reports_count = int((await session.execute(text("SELECT COUNT(*) FROM security_epp_report"))).scalar_one())
        safety_permits_count = int((await session.execute(text("SELECT COUNT(*) FROM safety_permit"))).scalar_one())
    except Exception:
        pass

    pending_company_retention = sum(
        float(item.amount or 0)
        for item in company_retentions
        if str(item.status or "").lower() in {"pendiente", "pending"}
    )
    pending_invoice_retention = sum(
        float(item.retention_amount or 0)
        for item in invoice_retentions
        if str(item.retention_status or "").lower() in {"pendiente", "pending"}
    )
    pending_total = round(pending_company_retention + pending_invoice_retention, 2)
    pending_procurement = [item for item in procurement if str(item.status or "").lower() in {"pendiente", "pending"}]
    pending_timesheets = [item for item in timesheets if str(item.status or "").lower() in {"pendiente", "pending"}]

    company_rifs = [
        (company.rif or "").strip()
        for company in companies
        if (company.rif or "").strip()
    ]

    return {
        "companies_count": len(companies),
        "vehicles_count": len(vehicles),
        "company_rifs": company_rifs,
        "pending_retention_total": pending_total,
        "pending_retention_count": len(
            [
                item
                for item in company_retentions
                if str(item.status or "").lower() in {"pendiente", "pending"}
            ]
        ) + len(
            [
                item
                for item in invoice_retentions
                if str(item.retention_status or "").lower() in {"pendiente", "pending"}
            ]
        ),
        "users_count": len(users),
        "active_users_count": sum(1 for item in users if item.is_active),
        "inventory_items_count": len(inventory_items),
        "inventory_quantity": sum(int(item.quantity or 0) for item in inventory_items),
        "procurement_count": len(procurement),
        "pending_procurement_count": len(pending_procurement),
        "timesheets_count": len(timesheets),
        "pending_timesheets_count": len(pending_timesheets),
        "vehicle_inspections_count": len(inspections),
        "environment_talks_count": len(environmental_talks),
        "environment_drills_count": len(environmental_drills),
        "environment_documents_count": len(environmental_documents),
        "chat_messages_count": len(chat_messages),
        "security_reports_count": security_reports_count,
        "safety_permits_count": safety_permits_count,
    }


def _build_local_answer(question: str, snapshot: dict | None = None) -> str:
    lower = question.lower().strip()
    if not lower:
        return "Puedes preguntarme por cualquier módulo del sistema: stock, personal, seguridad, vehículos, reportes, administración, chat y más."

    system = snapshot or {}
    company_rifs = system.get("company_rifs") or []
    pending_total = system.get("pending_retention_total", 0)
    vehicles_count = system.get("vehicles_count", 0)
    companies_count = system.get("companies_count", 0)
    users_count = system.get("users_count", 0)
    active_users_count = system.get("active_users_count", 0)
    inventory_items_count = system.get("inventory_items_count", 0)
    inventory_quantity = system.get("inventory_quantity", 0)
    procurement_count = system.get("procurement_count", 0)
    pending_procurement_count = system.get("pending_procurement_count", 0)
    timesheets_count = system.get("timesheets_count", 0)
    pending_timesheets_count = system.get("pending_timesheets_count", 0)
    vehicle_inspections_count = system.get("vehicle_inspections_count", 0)
    security_reports_count = system.get("security_reports_count", 0)
    safety_permits_count = system.get("safety_permits_count", 0)
    environment_total = sum(int(system.get(key, 0) or 0) for key in (
        "environment_talks_count", "environment_drills_count", "environment_documents_count"
    ))
    chat_messages_count = system.get("chat_messages_count", 0)

    retention_keywords = [
        "retención pendiente",
        "retencion pendiente",
        "pendiente por cobrar",
        "por cobrar",
        "retenciones por cobrar",
        "retenciones pendientes",
        "retenciones pendientes por cobrar",
        "retenciones pendiente",
        "retencion por cobrar",
    ]
    rif_keywords = ["rif", "empresa", "empresas registradas", "empresas", "registro de empresas"]
    vehicle_keywords = [
        "vehículo",
        "vehiculo",
        "automóvil",
        "automovil",
        "flota",
        "vehículos registrados",
        "vehiculos registrados",
        "cuántos vehículos",
        "cuantos vehiculos",
        "carros",
        "vehículos",
        "vehiculos",
    ]

    matches_retention = any(keyword in lower for keyword in retention_keywords)
    matches_rif = any(keyword in lower for keyword in rif_keywords)
    matches_vehicle = any(keyword in lower for keyword in vehicle_keywords)

    pending_retention_count = int(system.get("pending_retention_count", 0) or 0)

    if any(keyword in lower for keyword in ["cuántos usuarios", "cuantos usuarios", "personal activo", "usuarios activos"]):
        return f"Hay {users_count} usuarios registrados y {active_users_count} usuarios activos en el sistema."
    if any(keyword in lower for keyword in ["cuántos productos", "cuantos productos", "cantidad de stock", "inventario actual"]):
        return f"El inventario tiene {inventory_items_count} productos registrados, con una existencia total de {inventory_quantity} unidades."
    if any(keyword in lower for keyword in ["procura pendiente", "compras pendientes", "solicitudes pendientes", "pedidos pendientes"]):
        return f"Hay {pending_procurement_count} solicitudes de procura pendientes de un total de {procurement_count}."
    if any(keyword in lower for keyword in ["hojas de tiempo pendientes", "timesheet pendientes", "horas pendientes", "tiempos pendientes"]):
        return f"Hay {pending_timesheets_count} hojas de tiempo pendientes de un total de {timesheets_count}."
    if any(keyword in lower for keyword in ["inspecciones", "inspección vehicular", "inspecciones vehiculares"]):
        return f"Hay {vehicle_inspections_count} inspecciones vehiculares registradas."
    if any(keyword in lower for keyword in ["ambiente", "ambientales", "simulacros", "capacitaciones ambientales"]):
        return f"El módulo de ambiente tiene {environment_total} registros entre capacitaciones, simulacros y documentos."
    if any(keyword in lower for keyword in ["mensajes", "chat interno", "conversaciones"]):
        return f"El chat interno tiene {chat_messages_count} mensajes registrados."

    if matches_retention or matches_rif or matches_vehicle:
        summaries = []
        if matches_retention:
            if pending_retention_count == 0:
                summaries.append("No tienes retenciones pendientes por cobrar en este momento.")
            else:
                summaries.append(
                    f"Tienes {pending_retention_count} retenciones pendientes por cobrar, con un total de {pending_total:.2f} Bs. "
                    "revisando tanto retenciones de empresa como facturas pendientes."
                )
        if matches_rif:
            if not company_rifs:
                summaries.append("Todavía no hay empresas registradas con RIF cargado en el sistema.")
            else:
                summary = ", ".join(company_rifs[:10])
                if len(company_rifs) > 10:
                    summary += f" y {len(company_rifs) - 10} más."
                summaries.append(f"Tienes {companies_count} empresas registradas. Sus RIFs son: {summary}.")
        if matches_vehicle:
            summaries.append(f"Actualmente tienes {vehicles_count} vehículos registrados en el sistema.")
        if summaries:
            return " ".join(summaries)

    if any(keyword in lower for keyword in ["retención pendiente", "retencion pendiente", "pendiente por cobrar", "por cobrar", "retenciones por cobrar", "retenciones pendientes"]):
        if pending_retention_count == 0:
            return "No tienes retenciones pendientes por cobrar en este momento."
        return (
            f"Tienes {pending_retention_count} retenciones pendientes por cobrar, con un total de {pending_total:.2f} Bs. "
            "revisando tanto retenciones de empresa como facturas pendientes."
        )

    if any(keyword in lower for keyword in ["rif", "empresa", "empresas registradas", "empresas", "registro de empresas"]):
        if not company_rifs:
            return "Todavía no hay empresas registradas con RIF cargado en el sistema."
        summary = ", ".join(company_rifs[:10])
        if len(company_rifs) > 10:
            summary += f" y {len(company_rifs) - 10} más."
        return f"Tienes {companies_count} empresas registradas. Sus RIFs son: {summary}."

    if any(keyword in lower for keyword in ["vehículo", "vehiculo", "automóvil", "automovil", "flota", "vehículos registrados", "cuántos vehículos", "cuantos vehiculos", "carros"]):
        return f"Actualmente tienes {vehicles_count} vehículos registrados en el sistema."

    if any(keyword in lower for keyword in ["stock", "inventario", "material", "existencia", "categoría", "categoria"]):
        return f"El inventario tiene {inventory_items_count} productos registrados y {inventory_quantity} unidades en existencia total."
    if any(keyword in lower for keyword in ["personal", "usuario", "empleado", "rol", "permiso", "dotación", "dotacion"]):
        return f"Personal tiene {users_count} usuarios registrados y {active_users_count} activos. Sus permisos y roles se administran desde el perfil de cada usuario."
    if any(keyword in lower for keyword in ["vehículo", "vehiculo", "flota", "unidad", "chofer", "conductor"]):
        return "El módulo de vehículos gestiona la flota, el estatus operativo, conductores, inspecciones y seguimiento del uso de cada unidad."
    if any(keyword in lower for keyword in ["seguridad", "epp", "permiso", "riesgo", "inspección", "inspeccion"]):
        return f"Seguridad tiene {security_reports_count} análisis EPP y {safety_permits_count} permisos procesados."
    if any(keyword in lower for keyword in ["ambiente", "residuos", "simulacro", "capacitación", "capacitacion"]):
        return "El módulo de ambiente se enfoca en capacitaciones, simulacros, residuos y cumplimiento ambiental para mantener seguimiento operativo y preventivo."
    if any(keyword in lower for keyword in ["reporte", "dashboard", "indicador", "estadística", "analítica"]):
        return f"Reportes consolida datos de {companies_count} empresas, {vehicles_count} vehículos, {inventory_items_count} productos y {procurement_count} solicitudes de procura."
    if any(keyword in lower for keyword in ["procura", "compras", "solicitud", "pedido", "proveedor"]):
        return f"Procura tiene {procurement_count} solicitudes registradas, de las cuales {pending_procurement_count} están pendientes."
    if any(keyword in lower for keyword in ["tiempo", "timesheet", "horas", "turno", "actividad"]):
        return f"Hay {timesheets_count} hojas de tiempo registradas y {pending_timesheets_count} pendientes."
    if any(keyword in lower for keyword in ["chat", "mensaje", "notificación", "notificacion", "comunicación", "comunicacion"]):
        return f"El chat interno tiene {chat_messages_count} mensajes registrados entre los usuarios del sistema."
    if any(keyword in lower for keyword in ["administración", "administracion", "empresa", "retención", "retencion", "peaje", "documento"]):
        return f"Administración tiene {companies_count} empresas registradas y {pending_retention_count} retenciones pendientes por un total de {pending_total:.2f} Bs."
    if any(keyword in lower for keyword in ["ayuda", "como", "qué", "que", "sistema", "general"]):
        return "El sistema está organizado en módulos de inventario, personal, vehículos, seguridad, ambiente, reportes, procura, tiempos, chat y administración, cada uno enfocado en una parte del negocio."
    return "Puedo ayudarte a consultar los módulos del sistema. Prueba preguntar por stock, personal, seguridad, vehículos, reportes, administración o chat para obtener orientación específica."


async def _ask_deepseek(question: str, snapshot: dict | None = None) -> tuple[str | None, Literal["deepseek", "local"]]:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return None, "local"

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions"),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
                    "temperature": 0.3,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Eres un asistente interno del sistema SOLAND. Responde en español, "
                                "de forma clara y breve, y referencia los módulos de la plataforma cuando aplique. "
                                "Si no sabes algo, dilo honestamente y usa la información disponible del sistema."
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                "Consulta sobre el sistema SOLAND. "
                                f"Datos actuales del sistema:\n{json.dumps(snapshot or {}, ensure_ascii=False)}\n\n"
                                f"Contexto\n{MODULE_CONTEXT}\n\nPregunta del usuario:\n{question}"
                            ),
                        },
                    ],
                },
            )
            response.raise_for_status()
            payload = response.json()
            answer = payload["choices"][0]["message"]["content"]
            return answer.strip() if answer and answer.strip() else None, "deepseek"
    except Exception:
        return None, "local"


@router.post("/ask", status_code=status.HTTP_200_OK)
async def ask_assistant(
    payload: AssistantQuery,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
):
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="La pregunta no puede estar vacía.")

    snapshot = await _get_system_snapshot(session)
    factual_keywords = (
        "cuánto", "cuanto", "cuántos", "cuantos", "pendiente", "existencia", "inventario",
        "stock", "retención", "retencion", "usuarios", "personal activo", "procura", "compras",
        "timesheet", "hoja de tiempo", "inspección", "inspeccion", "ambiente", "mensajes",
    )
    if any(keyword in question.lower() for keyword in factual_keywords):
        answer, provider = _build_local_answer(question, snapshot), "local"
    else:
        answer, provider = await _ask_deepseek(question, snapshot)
        if not answer:
            answer = _build_local_answer(question, snapshot)
            provider = "local"

    return {
        "answer": answer,
        "provider": provider,
        "module": "assistant",
        "user": user.username,
    }
