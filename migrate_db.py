"""
Database migration script to update the schema.
This will drop and recreate all tables with the new schema.
WARNING: This will delete all existing data!
"""
from app import app, db
import os

def migrate_database():
    """Drop all tables and recreate with new schema"""
    with app.app_context():
        print("Dropping all tables...")
        db.drop_all()
        print("Creating new tables with updated schema...")
        db.create_all()
        
        # Create default admin user
        from app import AppUser
        admin = AppUser(username='admin', email='admin@smartstock.com', role='admin')
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()
        print("Default admin user created: username='admin', password='admin123'")
        print("\nDatabase migration completed successfully!")
        print("You can now run the app and optionally seed sample data with: python seed.py")

if __name__ == '__main__':
    print("WARNING: This will delete all existing data and recreate the database.")
    migrate_database()

