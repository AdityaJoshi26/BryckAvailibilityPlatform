from pydantic import BaseModel
from typing import Optional
from datetime import date, datetime

class MachineBase(BaseModel):
    ip_address:         str
    ibmi_bmc:           Optional[str] = None
    hostname:           Optional[str] = None
    machine_type:       Optional[str] = None
    status:             Optional[str] = "Ready"
    priority:           Optional[int] = 2
    status_checked_at:  Optional[datetime] = None
    used_for:           Optional[str] = None
    allotted_to:        Optional[str] = None
    can_parallel:       Optional[bool] = False
    current_build:      Optional[str] = None
    tests_completed:    Optional[str] = None
    active_issues:      Optional[str] = None
    test_status:        Optional[dict[str, bool]] = None
    notes:              Optional[str] = None
    last_health_status: Optional[str] = None
    is_reachable:       Optional[bool] = None
    reachable_via:      Optional[str] = None
    customer:           Optional[str] = None
    shipping_date:      Optional[date] = None


class MachineCreate(MachineBase):
    pass

class MachineUpdate(MachineBase):
    ip_address: Optional[str] = None   # allow partial updates

class MachineOut(MachineBase):
    id:         int
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True
