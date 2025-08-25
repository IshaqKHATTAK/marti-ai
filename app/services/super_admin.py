from sqlalchemy.orm import Session
from app.models.user import User, UserRole
from datetime import datetime
from app.common.security import SecurityManager
from app.common.database_config import get_async_db

def create_super_admin(db: Session):
    # Check if the user already exists
    existing_user = db.query(User).filter(User.email == "superadmin@askmarti.com").first()
    if existing_user:
        print("Super admin already exists.")
        return
    security_manager = SecurityManager()
    # Create a new super admin user 
    hashed_password = security_manager.get_password_hash("Pa$$w0rd")
    super_admin = User(
        name="Super Admin",
        email="superadmin@askmarti.com",  
        hashed_password=hashed_password,
        role=UserRole.SUPER_ADMIN.value,
        is_active=True,
        is_verified=True,
        verified_at=datetime.utcnow(), 
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    
    # Add the user to the session and commit
    db.add(super_admin)
    db.commit()
    db.refresh(super_admin)
    
    print(f"Super Admin {super_admin.name} created successfully.")

