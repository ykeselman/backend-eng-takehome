"""
This is a test harness for the FastAPI app implemented in main.py.

It's also mostly written by Anthropic's Claude Sonnet 4.

It uses a test DB instead of the real one.

All tests are passing.

To run: pytest -s test_leads.py

"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os

from main import app, get_db, Base, Lead, LeadState

# Create test database
SQLALCHEMY_DATABASE_URL = "sqlite:///./test_leads.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

# Override the dependency
app.dependency_overrides[get_db] = override_get_db

# Test client
client = TestClient(app)

# Valid API key for testing
VALID_API_KEY = "attorney-key-123"
INVALID_API_KEY = "invalid-key"

@pytest.fixture(scope="function")
def setup_database():
    """Create fresh database for each test"""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

@pytest.fixture
def sample_lead_data():
    """Sample lead data for testing"""
    return {
        "first_name": "John",
        "last_name": "Doe",
        "email": "john.doe@test.com",
        "resume_s3_path": "s3://test-bucket/john-doe-resume.pdf"
    }

@pytest.fixture
def headers_valid():
    """Valid authorization headers"""
    return {"Authorization": f"Bearer {VALID_API_KEY}"}

@pytest.fixture
def headers_invalid():
    """Invalid authorization headers"""
    return {"Authorization": f"Bearer {INVALID_API_KEY}"}

class TestCreateLead:
    """Tests for POST /leads endpoint"""
    
    def test_create_lead_success(self, setup_database, sample_lead_data, headers_valid):
        """Test successful lead creation"""
        response = client.post("/leads", json=sample_lead_data, headers=headers_valid)
        
        assert response.status_code == 200
        data = response.json()
        
        assert data["first_name"] == sample_lead_data["first_name"]
        assert data["last_name"] == sample_lead_data["last_name"]
        assert data["email"] == sample_lead_data["email"]
        assert data["resume_s3_path"] == sample_lead_data["resume_s3_path"]
        assert data["state"] == "PENDING"
        assert "id" in data
        assert "created_at" in data
        assert "updated_at" in data
    
    def test_create_lead_duplicate_email(self, setup_database, sample_lead_data, headers_valid):
        """Test creating lead with duplicate email fails"""
        # Create first lead
        client.post("/leads", json=sample_lead_data, headers=headers_valid)
        
        # Try to create second lead with same email
        response = client.post("/leads", json=sample_lead_data, headers=headers_valid)
        
        assert response.status_code == 400
        assert "Lead with this email already exists" in response.json()["detail"]
    
    def test_create_lead_invalid_email(self, setup_database, headers_valid):
        """Test creating lead with invalid email format"""
        invalid_data = {
            "first_name": "John",
            "last_name": "Doe",
            "email": "invalid-email",
            "resume_s3_path": "s3://test-bucket/resume.pdf"
        }
        
        response = client.post("/leads", json=invalid_data, headers=headers_valid)
        assert response.status_code == 422
    
    def test_create_lead_unauthorized(self, setup_database, sample_lead_data, headers_invalid):
        """Test creating lead without valid authorization"""
        response = client.post("/leads", json=sample_lead_data, headers=headers_invalid)
        assert response.status_code == 401
    
    def test_create_lead_missing_fields(self, setup_database, headers_valid):
        """Test creating lead with missing required fields"""
        incomplete_data = {
            "first_name": "John",
            "email": "john@test.com"
            # Missing last_name and resume_s3_path
        }
        
        response = client.post("/leads", json=incomplete_data, headers=headers_valid)
        assert response.status_code == 422

class TestGetLeads:
    """Tests for GET /leads endpoint"""
    
    @pytest.fixture
    def create_sample_leads(self, setup_database):
        """Create multiple leads for testing"""
        db = TestingSessionLocal()
        leads = [
            Lead(
                first_name="John", 
                last_name="Doe", 
                email="john@test.com",
                resume_s3_path="s3://bucket/john.pdf",
                state=LeadState.PENDING
            ),
            Lead(
                first_name="Jane", 
                last_name="Smith", 
                email="jane@test.com",
                resume_s3_path="s3://bucket/jane.pdf",
                state=LeadState.REACHED_OUT
            ),
            Lead(
                first_name="Bob", 
                last_name="Wilson", 
                email="bob@test.com",
                resume_s3_path="s3://bucket/bob.pdf",
                state=LeadState.PENDING
            )
        ]
        
        for lead in leads:
            db.add(lead)
        db.commit()
        db.close()
        return leads
    
    def test_get_all_leads(self, create_sample_leads, headers_valid):
        """Test getting all leads"""
        response = client.get("/leads", headers=headers_valid)
        
        assert response.status_code == 200
        data = response.json()
        
        assert len(data) == 3
        assert all("id" in lead for lead in data)
        assert all("first_name" in lead for lead in data)
    
    def test_get_leads_filtered_by_state(self, create_sample_leads, headers_valid):
        """Test getting leads filtered by state"""
        response = client.get("/leads?state=PENDING", headers=headers_valid)
        
        assert response.status_code == 200
        data = response.json()
        
        assert len(data) == 2  # Only PENDING leads
        assert all(lead["state"] == "PENDING" for lead in data)
    
    def test_get_leads_with_pagination(self, create_sample_leads, headers_valid):
        """Test getting leads with pagination"""
        response = client.get("/leads?skip=1&limit=1", headers=headers_valid)
        
        assert response.status_code == 200
        data = response.json()
        
        assert len(data) == 1
    
    def test_get_leads_unauthorized(self, create_sample_leads, headers_invalid):
        """Test getting leads without authorization"""
        response = client.get("/leads", headers=headers_invalid)
        assert response.status_code == 401

class TestGetSingleLead:
    """Tests for GET /leads/{id} endpoint"""
    
    @pytest.fixture
    def create_single_lead(self, setup_database):
        """Create a single lead for testing"""
        db = TestingSessionLocal()
        lead = Lead(
            first_name="Test",
            last_name="User",
            email="test@example.com",
            resume_s3_path="s3://bucket/test.pdf",
            state=LeadState.PENDING
        )
        db.add(lead)
        db.commit()
        db.refresh(lead)
        lead_id = lead.id
        db.close()
        return lead_id
    
    def test_get_lead_by_id_success(self, create_single_lead, headers_valid):
        """Test getting a lead by valid ID"""
        lead_id = create_single_lead
        response = client.get(f"/leads/{lead_id}", headers=headers_valid)
        
        assert response.status_code == 200
        data = response.json()
        
        assert data["id"] == lead_id
        assert data["first_name"] == "Test"
        assert data["last_name"] == "User"
        assert data["email"] == "test@example.com"
    
    def test_get_lead_by_id_not_found(self, setup_database, headers_valid):
        """Test getting a lead by non-existent ID"""
        response = client.get("/leads/999", headers=headers_valid)
        
        assert response.status_code == 404
        assert "Lead not found" in response.json()["detail"]
    
    def test_get_lead_by_id_unauthorized(self, create_single_lead, headers_invalid):
        """Test getting a lead without authorization"""
        lead_id = create_single_lead
        response = client.get(f"/leads/{lead_id}", headers=headers_invalid)
        assert response.status_code == 401

class TestUpdateLead:
    """Tests for PUT /leads/{id} endpoint"""
    
    @pytest.fixture
    def create_lead_for_update(self, setup_database):
        """Create a lead for update testing"""
        db = TestingSessionLocal()
        lead = Lead(
            first_name="Original",
            last_name="Name",
            email="original@test.com",
            resume_s3_path="s3://bucket/original.pdf",
            state=LeadState.PENDING
        )
        db.add(lead)
        db.commit()
        db.refresh(lead)
        lead_id = lead.id
        db.close()
        return lead_id
    
    def test_update_lead_success(self, create_lead_for_update, headers_valid):
        """Test successful lead update"""
        lead_id = create_lead_for_update
        update_data = {
            "first_name": "Updated",
            "last_name": "Name",
            "state": "REACHED_OUT"
        }
        
        response = client.put(f"/leads/{lead_id}", json=update_data, headers=headers_valid)
        
        assert response.status_code == 200
        data = response.json()
        
        assert data["first_name"] == "Updated"
        assert data["state"] == "REACHED_OUT"
        assert data["email"] == "original@test.com"  # Unchanged field
    
    def test_update_lead_not_found(self, setup_database, headers_valid):
        """Test updating non-existent lead"""
        update_data = {"first_name": "Updated"}
        response = client.put("/leads/999", json=update_data, headers=headers_valid)
        
        assert response.status_code == 404
        assert "Lead not found" in response.json()["detail"]
    
    def test_update_lead_unauthorized(self, create_lead_for_update, headers_invalid):
        """Test updating lead without authorization"""
        lead_id = create_lead_for_update
        update_data = {"first_name": "Updated"}
        response = client.put(f"/leads/{lead_id}", json=update_data, headers=headers_invalid)
        assert response.status_code == 401

class TestUpdateLeadState:
    """Tests for PATCH /leads/{id}/state endpoint"""
    
    @pytest.fixture
    def create_lead_for_state_update(self, setup_database):
        """Create a lead for state update testing"""
        db = TestingSessionLocal()
        lead = Lead(
            first_name="State",
            last_name="Test",
            email="state@test.com",
            resume_s3_path="s3://bucket/state.pdf",
            state=LeadState.PENDING
        )
        db.add(lead)
        db.commit()
        db.refresh(lead)
        lead_id = lead.id
        db.close()
        return lead_id
    
    def test_update_lead_state_success(self, create_lead_for_state_update, headers_valid):
        """Test successful lead state update"""
        lead_id = create_lead_for_state_update
        
        response = client.patch(
            f"/leads/{lead_id}/state?new_state=REACHED_OUT", 
            headers=headers_valid
        )
        
        assert response.status_code == 200
        data = response.json()
        
        assert data["state"] == "REACHED_OUT"
        assert data["first_name"] == "State"  # Other fields unchanged
    
    def test_update_lead_state_not_found(self, setup_database, headers_valid):
        """Test updating state of non-existent lead"""
        response = client.patch(
            "/leads/999/state?new_state=REACHED_OUT", 
            headers=headers_valid
        )
        
        assert response.status_code == 404
        assert "Lead not found" in response.json()["detail"]
    
    def test_update_lead_state_unauthorized(self, create_lead_for_state_update, headers_invalid):
        """Test updating lead state without authorization"""
        lead_id = create_lead_for_state_update
        response = client.patch(
            f"/leads/{lead_id}/state?new_state=REACHED_OUT", 
            headers=headers_invalid
        )
        assert response.status_code == 401

class TestHealthCheck:
    """Tests for GET /health endpoint"""
    
    def test_health_check(self):
        """Test health check endpoint (no auth required)"""
        response = client.get("/health")
        
        assert response.status_code == 200
        data = response.json()
        
        assert data["status"] == "healthy"
        assert "timestamp" in data

# Cleanup after all tests
@pytest.fixture(scope="session", autouse=True)
def cleanup():
    """Clean up test database after all tests"""
    yield
    try:
        os.remove("./test_leads.db")
    except FileNotFoundError:
        pass
