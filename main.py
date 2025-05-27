from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Enum
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from datetime import datetime, timezone
from typing import List, Optional
import enum
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Database setup
SQLALCHEMY_DATABASE_URL = "sqlite:///./leads.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Lead state enum
class LeadState(str, enum.Enum):
    PENDING = "PENDING"
    REACHED_OUT = "REACHED_OUT"

def utcnow():
    return datetime.now(timezone.utc)

# Database Models
class Lead(Base):
    __tablename__ = "leads"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    resume_s3_path = Column(String, nullable=False)  # S3 full path
    state = Column(Enum(LeadState), default=LeadState.PENDING, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

# Create tables
Base.metadata.create_all(bind=engine)

# Pydantic Models (Request/Response schemas)
class LeadCreate(BaseModel):
    first_name: str
    last_name: str
    email: EmailStr
    resume_s3_path: str

class LeadUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[EmailStr] = None
    resume_s3_path: Optional[str] = None
    state: Optional[LeadState] = None

class LeadResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    email: str
    resume_s3_path: str
    state: LeadState
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

# Authentication
API_KEYS = {"attorney-key-123", "admin-key-456"}  # In production, store securely
security = HTTPBearer()

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if credentials.credentials not in API_KEYS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials

# Database dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Email service (stubs)
class EmailService:
    @staticmethod
    def send_prospect_email(email: str, first_name: str, last_name: str):
        """Send confirmation email to prospect"""
        logger.info(f"[EMAIL STUB] Sending confirmation email to prospect: {email}")
        logger.info(f"Subject: Thank you for your interest, {first_name}!")
        logger.info(f"Body: Dear {first_name} {last_name}, thank you for submitting your information. We will review your resume and get back to you soon.")
        
    @staticmethod
    def send_attorney_email(prospect_email: str, first_name: str, last_name: str, resume_path: str):
        """Send notification email to attorney"""
        logger.info("[EMAIL STUB] Sending notification email to attorney")
        logger.info("Subject: New Lead Submitted")
        logger.info(f"Body: A new lead has been submitted:\nName: {first_name} {last_name}\nEmail: {prospect_email}\nResume: {resume_path}")

# FastAPI app
app = FastAPI(title="Lead Management System", version="1.0.0")

@app.post("/leads", response_model=LeadResponse)
def create_lead(lead: LeadCreate, db: Session = Depends(get_db), api_key: str = Depends(get_current_user)):
    """Create a new lead"""
    
    # Check if email already exists
    existing_lead = db.query(Lead).filter(Lead.email == lead.email).first()
    if existing_lead:
        raise HTTPException(status_code=400, detail="Lead with this email already exists")
    
    # Create new lead
    db_lead = Lead(
        first_name=lead.first_name,
        last_name=lead.last_name,
        email=lead.email,
        resume_s3_path=lead.resume_s3_path,
        state=LeadState.PENDING
    )
    
    db.add(db_lead)
    db.commit()
    db.refresh(db_lead)
    
    # Send emails
    try:
        EmailService.send_prospect_email(lead.email, lead.first_name, lead.last_name)
        EmailService.send_attorney_email(lead.email, lead.first_name, lead.last_name, lead.resume_s3_path)
    except Exception as e:
        logger.error(f"Failed to send emails: {str(e)}")
        # Don't fail the request if email fails
    
    return db_lead

@app.get("/leads", response_model=List[LeadResponse])
def get_leads(
    skip: int = 0, 
    limit: int = 100, 
    state: Optional[LeadState] = None,
    db: Session = Depends(get_db), 
    api_key: str = Depends(get_current_user)
):
    """Get list of leads with optional filtering by state"""

    assert api_key
    query = db.query(Lead)
    
    if state:
        query = query.filter(Lead.state == state)
    
    leads = query.offset(skip).limit(limit).all()
    return leads

@app.get("/leads/{lead_id}", response_model=LeadResponse)
def get_lead(lead_id: int, db: Session = Depends(get_db), api_key: str = Depends(get_current_user)):
    """Get a specific lead by ID"""
    
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    return lead

@app.put("/leads/{lead_id}", response_model=LeadResponse)
def update_lead(
    lead_id: int, 
    lead_update: LeadUpdate, 
    db: Session = Depends(get_db), 
    api_key: str = Depends(get_current_user)
):
    """Update a lead (including state transitions)"""
    
    assert api_key
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    # Update fields that were provided
    update_data = lead_update.model_dump(exclude_unset=True)
    
    for field, value in update_data.items():
        setattr(lead, field, value)
    
    # Update the updated_at timestamp
    lead.updated_at = utcnow()
    
    db.commit()
    db.refresh(lead)
    
    return lead

@app.patch("/leads/{lead_id}/state", response_model=LeadResponse)
def update_lead_state(
    lead_id: int, 
    new_state: LeadState, 
    db: Session = Depends(get_db), 
    api_key: str = Depends(get_current_user)
):
    """Update only the state of a lead (convenience endpoint for attorneys)"""
    
    assert api_key
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    lead.state = new_state
    lead.updated_at = utcnow()
    
    db.commit()
    db.refresh(lead)
    
    logger.info(f"Lead {lead_id} state updated to {new_state} by attorney")
    
    return lead

@app.get("/health")
def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": utcnow()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
