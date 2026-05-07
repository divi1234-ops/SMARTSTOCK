"""
Seed script to populate the database with sample data for demo and testing.
Run this script to create sample users, inventories, products, and transactions.
"""
from app import app, db, AppUser, User, Inventory, Product, Transaction
from datetime import datetime, timedelta
import random

def seed_database():
    """Populate the database with sample data"""
    with app.app_context():
        # Clear existing data (optional - comment out if you want to keep existing data)
        print("Clearing existing data...")
        Transaction.query.delete()
        Product.query.delete()
        Inventory.query.delete()
        User.query.delete()
        
        # Don't delete AppUser to preserve login accounts
        # AppUser.query.filter(AppUser.username != 'admin').delete()
        
        db.session.commit()
        print("Existing data cleared.")
        
        # Create sample inventory managers (User model)
        print("Creating sample users...")
        users_data = [
            {'name': 'John Manager', 'email': 'john.manager@smartstock.com'},
            {'name': 'Sarah Smith', 'email': 'sarah.smith@smartstock.com'},
            {'name': 'Mike Johnson', 'email': 'mike.johnson@smartstock.com'},
            {'name': 'Emily Davis', 'email': 'emily.davis@smartstock.com'},
        ]
        
        users = []
        for user_data in users_data:
            user = User(**user_data)
            db.session.add(user)
            users.append(user)
        
        db.session.commit()
        print(f"Created {len(users)} users.")
        
        # Create sample inventories
        print("Creating sample inventories...")
        inventories_data = [
            {'name': 'Main Warehouse', 'location': 'Building A, Floor 1', 'user_id': users[0].id},
            {'name': 'North Storage', 'location': 'Building B, Floor 2', 'user_id': users[1].id},
            {'name': 'South Warehouse', 'location': 'Building C, Ground Floor', 'user_id': users[2].id},
            {'name': 'Distribution Center', 'location': 'Industrial Park, Zone 3', 'user_id': users[3].id},
        ]
        
        inventories = []
        for inv_data in inventories_data:
            inventory = Inventory(**inv_data)
            db.session.add(inventory)
            inventories.append(inventory)
        
        db.session.commit()
        print(f"Created {len(inventories)} inventories.")
        
        # Create sample products
        print("Creating sample products...")
        categories = ['Raw Materials', 'Finished Goods', 'Packaging', 'Electronics', 'Office Supplies', 'Food Items']
        product_names = [
            'Wheat Flour', 'Sugar', 'Salt', 'Olive Oil', 'Rice', 'Pasta',
            'Printed Boxes', 'Plastic Bags', 'Labels', 'Tape',
            'Laptops', 'Keyboards', 'Mice', 'Monitors', 'Cables',
            'Paper', 'Pens', 'Folders', 'Staplers', 'Notebooks',
            'Bread', 'Milk', 'Eggs', 'Butter', 'Cheese',
            'Cotton Fabric', 'Thread', 'Buttons', 'Zippers',
            'Steel Sheets', 'Aluminum Rods', 'Copper Wire',
        ]
        
        products = []
        for i, name in enumerate(product_names):
            category = random.choice(categories)
            inventory = random.choice(inventories)
            quantity = random.randint(0, 500)
            min_threshold = random.randint(5, 50)
            
            product = Product(
                name=name,
                category=category,
                quantity=quantity,
                min_threshold=min_threshold,
                inventory_id=inventory.id
            )
            db.session.add(product)
            products.append(product)
        
        db.session.commit()
        print(f"Created {len(products)} products.")
        
        # Create sample transactions
        print("Creating sample transactions...")
        # Get or create a staff user for transactions
        staff_user = AppUser.query.filter_by(username='admin').first()
        if not staff_user:
            # Try to find any existing staff user
            staff_user = AppUser.query.filter_by(role='staff').first()
            if not staff_user:
                staff_user = AppUser(username='staff1', email='staff1@smartstock.com', role='staff')
                staff_user.set_password('staff123')
                db.session.add(staff_user)
                db.session.commit()
                print("Created staff user for transactions.")
        
        actions = ['purchase', 'use']
        notes_samples = [
            'Restocked from supplier',
            'Used in production',
            'Customer order',
            'Internal use',
            'Emergency restock',
            'Regular maintenance',
            None, None, None  # More None to make it less frequent
        ]
        
        # Create transactions for the last 30 days
        for day in range(30):
            date = datetime.utcnow() - timedelta(days=day)
            num_transactions = random.randint(5, 20)
            
            for _ in range(num_transactions):
                product = random.choice(products)
                action = random.choice(actions)
                quantity = random.randint(1, 100)
                notes = random.choice(notes_samples)
                
                transaction = Transaction(
                    product_id=product.id,
                    quantity=quantity,
                    action=action,
                    timestamp=date - timedelta(hours=random.randint(0, 23), minutes=random.randint(0, 59)),
                    user_id=staff_user.id if random.random() > 0.3 else None,
                    username=staff_user.username if random.random() > 0.3 else random.choice(['Admin', 'System', 'Auto']),
                    notes=notes
                )
                db.session.add(transaction)
        
        db.session.commit()
        print(f"Created sample transactions for the last 30 days.")
        
        print("\n" + "="*50)
        print("SEEDING COMPLETE!")
        print("="*50)
        print(f"Users (Managers): {User.query.count()}")
        print(f"Inventories: {Inventory.query.count()}")
        print(f"Products: {Product.query.count()}")
        print(f"Transactions: {Transaction.query.count()}")
        print("\nYou can now log in and explore the sample data.")
        print("Default admin login: username='admin', password='admin123'")
        print("="*50)


if __name__ == '__main__':
    seed_database()

