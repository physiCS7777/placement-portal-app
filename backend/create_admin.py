from app import app, db, User, CompanyProfile
from werkzeug.security import generate_password_hash

# # We have to tell Flask we are working inside the app's context to touch the database
# with app.app_context():
#     # 1. Check if an admin already exists so we don't accidentally make duplicates
#     existing_admin = User.query.filter_by(username='admin').first()
    
#     if existing_admin:
#         print("Admin user already exists. No new admin created.")
#     else:
#         # 2. Create a new admin user with a hashed password for security
#         admin_user = User(
#             username='admin',
#             password = generate_password_hash('admin123', method='pbkdf2:sha256'),  # You can change this password as needed
#             role='admin'
#         )
        
#         # 3. Add the new admin user to the database and commit the changes
#         db.session.add(admin_user)
#         db.session.commit()
        
#         print("Admin user created successfully.")
        
# --- The rest of the app.py code remains unchanged ---


with app.app_context():
    # 1. Clear out everything and start fresh (for testing purposes)
    db.drop_all()
    db.create_all()
     
    # 2. Recreate the admin
    admin_user= User(
        username = 'admin',
        password_hash = generate_password_hash('admin123', method='pbkdf2:sha256'),
        role = 'admin'
    )
    db.session.add(admin_user)
    db.session.commit() # Save the admin
    
    # Create dummy company 1 user account
    if not User.query.filter_by(username='google_user').first():
        comp_user1 = User(username='google_user', password_hash=generate_password_hash('password123', method='pbkdf2:sha256'), role='company')
        db.session.add(comp_user1)
        db.session.commit()
        
        # Link to company profile
        c1 = CompanyProfile(name="Google India", industry="Technology", status="Pending", user_id=comp_user1.id)
        db.session.add(c1)

    # Create dummy company 2 user account
    if not User.query.filter_by(username='tata_user').first():
        comp_user2 = User(username='tata_user', password_hash=generate_password_hash('password123', method='pbkdf2:sha256'), role='company')
        db.session.add(comp_user2)
        db.session.commit()
        
        # Link to company profile
        c2 = CompanyProfile(name="Tata Consultancy Services", industry="Consulting", status="Pending", user_id=comp_user2.id)
        db.session.add(c2)

    db.session.commit()
    print("Database populated with test companies!")