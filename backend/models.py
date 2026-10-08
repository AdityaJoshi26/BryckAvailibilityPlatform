from sqlalchemy import Column, Integer, String, Boolean, Date, DateTime, Text, JSON
from sqlalchemy.sql import func
from database import Base

class Machine(Base):
    __tablename__ = "machines"

    id                = Column(Integer, primary_key=True, index=True)
    ip_address        = Column(String(45), unique=True, nullable=False)
    ibmi_bmc          = Column(String(45), unique=True)
    hostname          = Column(String(255))
    machine_type      = Column(String(50))
    status            = Column(String(50), default="Ready")
    priority          = Column(Integer, default=2, nullable=False)
    status_checked_at = Column(DateTime)
    used_for          = Column(String(50))
    allotted_to       = Column(String(150))
    can_parallel      = Column(Boolean, default=False)
    current_build     = Column(String(100))
    tests_completed   = Column(String(50))
    active_issues     = Column(Text)
    test_status       = Column(JSON, default=dict)
    notes             = Column(Text)
    last_health_status = Column(String(50))
    is_reachable      = Column(Boolean)
    reachable_via     = Column(String(20))
    customer          = Column(String(255))
    shipping_date     = Column(Date)
    created_at        = Column(DateTime, default=func.now())
    updated_at        = Column(DateTime, default=func.now(), onupdate=func.now())
    

