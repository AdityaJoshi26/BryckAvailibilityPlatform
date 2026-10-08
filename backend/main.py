from fastapi import FastAPI, Depends, HTTPException, Query 
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime

import asyncio

import logging
import os
from pathlib import Path
from fastapi import Form, Depends
from sqlalchemy.orm import Session
from fastapi.responses import JSONResponse

from database import Base, engine, get_db, SessionLocal
from models import Machine
from schemas import MachineCreate, MachineUpdate, MachineOut

# Base.metadata.drop_all(bind=engine)   

TEST_SUITES = [
    "Config Shipment Test",
    "Stress Shipment Test",
    "Web Shipment Test",
    "Reboot Shipment Test",
    "Cloud Transfer Test"
]
# [test_config_shipment, test_stress_shipment, test_web_shipment, test_reboot_shipment, test_cloud_transfer_shipment]
PRIORITY_OPTIONS = [
    {"label": "High", "value": 0},
    {"label": "Medium", "value": 1},
    {"label": "Low", "value": 2},
]

Base.metadata.create_all(bind=engine)


def ensure_machine_columns() -> None:
    inspector = inspect(engine)
    if "machines" not in inspector.get_table_names():
        return

    existing_columns = {col["name"] for col in inspector.get_columns("machines")}
    with engine.begin() as conn:
        if "ibmi_bmc" not in existing_columns:
            conn.execute(text("ALTER TABLE machines ADD COLUMN ibmi_bmc VARCHAR(45)"))
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_machines_ibmi_bmc "
                    "ON machines (ibmi_bmc)"
                )
            )
        if "test_status" not in existing_columns:
            conn.execute(text("ALTER TABLE machines ADD COLUMN test_status JSON"))
        if "priority" not in existing_columns:
            conn.execute(text("ALTER TABLE machines ADD COLUMN priority INTEGER DEFAULT 2"))
            conn.execute(text("UPDATE machines SET priority = 2 WHERE priority IS NULL"))


def normalize_test_status(test_status: Optional[dict[str, bool]]) -> Optional[dict[str, bool]]:
    if test_status is None:
        return None
    if not isinstance(test_status, dict):
        raise HTTPException(status_code=422, detail="test_status must be an object")

    normalized: dict[str, bool] = {}
    for suite, completed in test_status.items():
        if suite not in TEST_SUITES:
            raise HTTPException(status_code=422, detail=f"Unknown test suite: {suite}")
        if not isinstance(completed, bool):
            raise HTTPException(status_code=422, detail=f"test_status[{suite}] must be true/false")
        normalized[suite] = completed
    return normalized


def normalize_priority(priority: Optional[int]) -> Optional[int]:
    if priority is None:
        return None
    if priority not in {0, 1, 2}:
        raise HTTPException(status_code=422, detail="priority must be 0 (High), 1 (Medium), or 2 (Low)")
    return priority


ensure_machine_columns()

app = FastAPI(title="Bryck Inventory API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Inventory: List & Filter ──────────────────────────────────────
@app.get("/inventory", response_model=list[MachineOut])
def list_machines(
    status:       Optional[str] = Query(None),
    priority:     Optional[int] = Query(None),
    used_for:     Optional[str] = Query(None),
    machine_type: Optional[str] = Query(None),
    allotted_to:  Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    q = db.query(Machine)
    if status:       q = q.filter(Machine.status == status)
    if priority is not None:
        normalized_priority = normalize_priority(priority)
        q = q.filter(Machine.priority == normalized_priority)
    if used_for:     q = q.filter(Machine.used_for == used_for)
    if machine_type: q = q.filter(Machine.machine_type == machine_type)
    if allotted_to:  q = q.filter(Machine.allotted_to == allotted_to)
    return q.order_by(Machine.created_at.desc()).all()


# ── Inventory: Get single machine ─────────────────────────────────
@app.get("/inventory/{machine_id}", response_model=MachineOut)
def get_machine(machine_id: int, db: Session = Depends(get_db)):
    machine = db.query(Machine).filter(Machine.id == machine_id).first()
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not found")
    return machine


# ── Inventory: Add machine ────────────────────────────────────────
@app.post("/inventory", response_model=MachineOut, status_code=201)
def add_machine(payload: MachineCreate, db: Session = Depends(get_db)):
    existing = db.query(Machine).filter(Machine.ip_address == payload.ip_address).first()
    if existing:
        raise HTTPException(status_code=409, detail="IP already exists in inventory")
    if payload.ibmi_bmc:
        existing_bmc = db.query(Machine).filter(Machine.ibmi_bmc == payload.ibmi_bmc).first()
        if existing_bmc:
            raise HTTPException(status_code=409, detail="IBMi/BMC already exists in inventory")

    payload_data = payload.model_dump()
    payload_data["test_status"] = normalize_test_status(payload_data.get("test_status"))
    payload_data["priority"] = normalize_priority(payload_data.get("priority"))
    if payload_data["priority"] is None:
        payload_data["priority"] = 2

    machine = Machine(**payload_data)
    db.add(machine)
    db.commit()
    db.refresh(machine)
    return machine

@app.post("/slack/inv")
def slack_inventory(
    command: str = Form(...),
    text: str = Form(""),
    user_id: str = Form(...),
    db: Session = Depends(get_db),
):
    machines = db.query(Machine).order_by(Machine.created_at.desc()).all()

    headers = [
        "IP", "BMC", "Parallel", "Allotted To",
        "Status", "Type", "Build"
    ]

    rows = []

    for m in machines:
        rows.append([
            str(m.ip_address or "NA"),
            str(m.ibmi_bmc or "NA"),
            "Yes" if m.can_parallel else "No",
            str(m.allotted_to or "NA"),
            str(m.status or "NA"),
            str(m.machine_type or "NA"),
            str(m.current_build or "NA"),
        ])

    if rows:
        col_widths = [
            max(len(str(row[i])) for row in ([headers] + rows))
            for i in range(len(headers))
        ]

        header_line = " | ".join(
            headers[i].ljust(col_widths[i])
            for i in range(len(headers))
        )

        divider = "-+-".join(
            "-" * col_widths[i]
            for i in range(len(headers))
        )

        data_lines = [
            " | ".join(
                row[i].ljust(col_widths[i])
                for i in range(len(headers))
            )
            for row in rows
        ]

        table = "\n".join([header_line, divider] + data_lines)
        text_response = "*📦 Inventory Snapshot*\n```" + table + "```"
    else:
        text_response = "*📦 Inventory Snapshot*\n_No machines found._"

    return JSONResponse(
        content={
            "response_type": "in_channel",  # visible to entire channel
            "text": text_response
        }
    )
# ── Inventory: Edit machine (partial update) ──────────────────────
@app.patch("/inventory/{machine_id}", response_model=MachineOut)
def edit_machine(machine_id: int, payload: MachineUpdate, db: Session = Depends(get_db)):
    machine = db.query(Machine).filter(Machine.id == machine_id).first()
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not found")

    updates = payload.model_dump(exclude_unset=True)
    if "ip_address" in updates and updates["ip_address"] and updates["ip_address"] != machine.ip_address:
        existing = db.query(Machine).filter(Machine.ip_address == updates["ip_address"]).first()
        if existing and existing.id != machine_id:
            raise HTTPException(status_code=409, detail="IP already exists in inventory")

    if "ibmi_bmc" in updates and updates["ibmi_bmc"] and updates["ibmi_bmc"] != machine.ibmi_bmc:
        existing_bmc = db.query(Machine).filter(Machine.ibmi_bmc == updates["ibmi_bmc"]).first()
        if existing_bmc and existing_bmc.id != machine_id:
            raise HTTPException(status_code=409, detail="IBMi/BMC already exists in inventory")

    if "test_status" in updates:
        updates["test_status"] = normalize_test_status(updates["test_status"])
    if "priority" in updates:
        updates["priority"] = normalize_priority(updates["priority"])
        if updates["priority"] is None:
            updates["priority"] = 2

    for field, value in updates.items():
        setattr(machine, field, value)
    machine.updated_at = datetime.now()
    db.commit()
    db.refresh(machine)
    return machine


# ── Inventory: Remove machine ─────────────────────────────────────
@app.delete("/inventory/{machine_id}", status_code=204)
def remove_machine(machine_id: int, db: Session = Depends(get_db)):
    machine = db.query(Machine).filter(Machine.id == machine_id).first()
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not found")
    db.delete(machine)
    db.commit()


# ── Dropdown options (for frontend selects) ───────────────────────
@app.get("/inventory/options/status")
def status_options():
    return ["Active", "Ready", "Down", "Shipped", "Idle"]

@app.get("/inventory/options/used-for")
def used_for_options():
    return ["Testing", "Development", "Customer", "Idle"]

@app.get("/inventory/options/machine-type")
def machine_type_options():
    return ["Bryck", "BryckMini"]

@app.get("/inventory/options/test-suites")
def test_suite_options():
    return TEST_SUITES

@app.get("/inventory/options/priority")
def priority_options():
    return PRIORITY_OPTIONS

import json
import requests 
from fastapi import Request
SLACK_BOT_TOKEN = ""


@app.post("/slack/add_machine")
async def open_add_machine_modal(trigger_id: str = Form(...)):

    def text_input(block_id, label, action_id, optional=True, multiline=False):
        block = {
            "type": "input",
            "block_id": block_id,
            "label": {"type": "plain_text", "text": label},
            "element": {
                "type": "plain_text_input",
                "action_id": action_id,
                **({"multiline": True} if multiline else {})
            }
        }
        if optional:
            block["optional"] = True
        return block

    modal = {
        "type": "modal",
        "callback_id": "add_machine_modal",
        "title": {"type": "plain_text", "text": "Add Machine"},
        "submit": {"type": "plain_text", "text": "Add"},
        "blocks": [

            # Required field
            {
                "type": "input",
                "block_id": "ip_block",
                "label": {"type": "plain_text", "text": "IP Address"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "ip_input",
                    "placeholder": {"type": "plain_text", "text": "10.10.20.5"}
                }
            },

            {
                "type": "input",
                "block_id": "hostname_block",
                "optional": True,
                "label": {"type": "plain_text", "text": "Hostname"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "hostname_input"
                }
            },

            {
                "type": "input",
                "block_id": "bmc_block",
                "optional": True,
                "label": {"type": "plain_text", "text": "IBMi / BMC"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "bmc_input"
                }
            },

            # Machine type dropdown
            {
                "type": "input",
                "block_id": "machine_type_block",
                "optional": True,
                "label": {"type": "plain_text", "text": "Machine Type"},
                "element": {
                    "type": "static_select",
                    "action_id": "machine_type_input",
                    "options": [
                        {"text": {"type": "plain_text", "text": "Bryck"}, "value": "Bryck"},
                        {"text": {"type": "plain_text", "text": "BryckMini"}, "value": "BryckMini"}
                    ]
                }
            },

            # Status dropdown
            {
                "type": "input",
                "block_id": "status_block",
                "optional": True,
                "label": {"type": "plain_text", "text": "Status"},
                "element": {
                    "type": "static_select",
                    "action_id": "status_input",
                    "options": [
                        {"text": {"type": "plain_text", "text": "Active"}, "value": "Active"},
                        {"text": {"type": "plain_text", "text": "Ready"}, "value": "Ready"},
                        {"text": {"type": "plain_text", "text": "Down"}, "value": "Down"},
                        {"text": {"type": "plain_text", "text": "Shipped"}, "value": "Shipped"},
                        {"text": {"type": "plain_text", "text": "Idle"}, "value": "Idle"}
                    ]
                }
            },

            # Priority dropdown
            {
                "type": "input",
                "block_id": "priority_block",
                "optional": True,
                "label": {"type": "plain_text", "text": "Priority"},
                "element": {
                    "type": "static_select",
                    "action_id": "priority_input",
                    "options": [
                        {"text": {"type": "plain_text", "text": "High"}, "value": "0"},
                        {"text": {"type": "plain_text", "text": "Medium"}, "value": "1"},
                        {"text": {"type": "plain_text", "text": "Low"}, "value": "2"}
                    ]
                }
            },

            {
                "type": "input",
                "block_id": "used_for_block",
                "optional": True,
                "label": {"type": "plain_text", "text": "Used For"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "used_for_input"
                }
            },

            {
                "type": "input",
                "block_id": "allotted_block",
                "optional": True,
                "label": {"type": "plain_text", "text": "Allotted To"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "allotted_input"
                }
            },

            # Boolean toggle
            {
                "type": "input",
                "block_id": "parallel_block",
                "optional": True,
                "label": {"type": "plain_text", "text": "Can Run Parallel Tests"},
                "element": {
                    "type": "checkboxes",
                    "action_id": "parallel_input",
                    "options": [
                        {
                            "text": {"type": "plain_text", "text": "Enabled"},
                            "value": "true"
                        }
                    ]
                }
            },

            {
                "type": "input",
                "block_id": "build_block",
                "optional": True,
                "label": {"type": "plain_text", "text": "Current Build"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "build_input"
                }
            },

            {
                "type": "input",
                "block_id": "issues_block",
                "optional": True,
                "label": {"type": "plain_text", "text": "Active Issues"},
                "element": {
                    "type": "plain_text_input",
                    "multiline": True,
                    "action_id": "issues_input"
                }
            },

            {
                "type": "input",
                "block_id": "customer_block",
                "optional": True,
                "label": {"type": "plain_text", "text": "Customer"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "customer_input"
                }
            },

            {
                "type": "input",
                "block_id": "ship_block",
                "optional": True,
                "label": {"type": "plain_text", "text": "Shipping Date"},
                "element": {
                    "type": "datepicker",
                    "action_id": "ship_input"
                }
            },

            {
                "type": "input",
                "block_id": "notes_block",
                "optional": True,
                "label": {"type": "plain_text", "text": "Notes"},
                "element": {
                    "type": "plain_text_input",
                    "multiline": True,
                    "action_id": "notes_input"
                }
            }
        ]
    }

    response = requests.post(
        "https://slack.com/api/views.open",
        headers={
            "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
            "Content-Type": "application/json"
        },
        json={
            "trigger_id": trigger_id,
            "view": modal
        }
    )

    if not response.json().get("ok"):
        print("❌ Failed to open modal:", response.json())

    return "Machine adding"


# ─────────────────────────────────────────────
# STEP 2: Modal submission → insert into DB
# ─────────────────────────────────────────────

@app.post("/slack/inventory")
async def handle_modal_submission(
    request: Request,
    db: Session = Depends(get_db)
):

    form = await request.form()
    payload_raw = form.get("payload")

    if not payload_raw:
        print("❌ No payload received")
        return {"response_action": "clear"}

    payload = json.loads(payload_raw)

    print("\n===== SLACK MODAL SUBMISSION =====")
    print(payload)

    values = payload["view"]["state"]["values"]

    def get(block, action):
        return values.get(block, {}).get(action, {}).get("value")

    # ── Extract fields ─────────────────────
    ip            = get("ip_block", "ip_input")
    hostname      = get("hostname_block", "hostname_input")
    bmc           = get("bmc_block", "bmc_input")
    machine_type  = get("type_block", "type_input")
    status        = get("status_block", "status_input")
    priority_raw  = get("priority_block", "priority_input")
    used_for      = get("used_block", "used_input")
    allotted_to   = get("allotted_block", "allotted_input")
    customer      = get("customer_block", "customer_input")
    notes         = get("notes_block", "notes_input")
    ship_raw      = get("ship_block", "ship_input")

    # ── Required field validation ──────────
    if not ip:
        return {
            "response_action": "errors",
            "errors": {"ip_block": "IP address is required"}
        }

    # ── Type conversions ───────────────────
    priority = int(priority_raw) if priority_raw else None

    shipping_date = None
    if ship_raw:
        try:
            shipping_date = datetime.fromisoformat(ship_raw).date()
        except Exception:
            print("⚠️ Invalid date format:", ship_raw)

    # ── Create Machine object ──────────────
    machine_data = MachineCreate(
        ip_address=ip,
        hostname=hostname,
        ibmi_bmc=bmc,
        machine_type=machine_type,
        status=status,
        priority=priority,
        used_for=used_for,
        allotted_to=allotted_to,
        customer=customer,
        notes=notes,
        shipping_date=shipping_date
    )

    try:
        machine = add_machine(machine_data, db)
        print(f"✅ Machine inserted: {machine.ip_address}")
    except Exception as e:
        print("❌ Machine insert failed:", e)

    return {"response_action": "clear"}
