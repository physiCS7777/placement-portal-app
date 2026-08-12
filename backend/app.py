from email.policy import default
import os
import csv
from flask import Flask, jsonify, request, render_template
from flask import send_from_directory
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity, verify_jwt_in_request
from flask_cors import CORS
from datetime import datetime
from functools import wraps
from werkzeug.utils import secure_filename
from celery import Celery
from flask_caching import Cache
import threading
import csv
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders



# 🌟 Configure the Resume Upload Directory
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True) # Automatically creates the folders if they don't exist

# --- Tells Flask where the frontend folder is located ---
base_dir = os.path.abspath(os.path.dirname(__file__))
frontend_dir = os.path.join(base_dir, '..', 'frontend')


# This initializes the Flask application, pointing it to the frontend directory for serving static files and templates.
app = Flask(__name__, template_folder=frontend_dir, static_folder=frontend_dir)

# 2. NEW: Enable CORS for all routes and allow the 'Authorization' header explicitly
CORS(app, resources={r"/api/*": {"origins": "*", "allow_headers": ["Content-Type", "Authorization"]}})


# --- Database Configuration ---
# this tells Flask to create a SQLite database named 'placement.db' in my backend folder.
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///placement.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET_KEY'] = 'super-secret-key-that-is-way-longer-than-32-bytes-long'  # Change this to a strong secret key in production

# ADD THIS LINE RIGHT HERE:
app.config['JWT_COOKIE_CSRF_PROTECT'] = False
app.config['JWT_TOKEN_LOCATION'] = ['headers']  # Explicitly tell Flask to ONLY look in headers
app.config['JWT_HEADER_NAME'] = 'Authorization' # Explicitly set the header name
app.config['JWT_HEADER_TYPE'] = 'Bearer'        # Explicitly set the token type prefix

# Configure Flask-Caching to harness your active Redis instance
app.config['CACHE_TYPE'] = 'RedisCache'
app.config['CACHE_REDIS_URL'] = 'redis://localhost:6379/0'
app.config['CACHE_DEFAULT_TIMEOUT'] = 300 # 🌟 Implements a strict 5-minute cache expiry

cache = Cache(app)

# Configure Celery to use Redis as both the message broker and result storage
app.config['CELERY_BROKER_URL'] = 'redis://localhost:6379/0'
app.config['CELERY_RESULT_BACKEND'] = 'redis://localhost:6379/0'

# Instantiate Celery
celery = Celery(app.name, broker=app.config['CELERY_BROKER_URL'])
celery.conf.update(app.config)

# Ensure Celery tasks run inside the Flask Application Context
class ContextTask(celery.Task):
    def __call__(self, *args, **kwargs):
        with app.app_context():
            return self.run(*args, **kwargs)
celery.Task = ContextTask

# Initialize the database
db = SQLAlchemy(app)
jwt = JWTManager(app)


# --- Database Models(Tables) ---
# 1. Unifier User Table for Logins
class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False) # 'admin', 'company', or 'student'
    is_active = db.Column(db.Boolean, default=True)
    is_blacklisted = db.Column(db.Boolean, default=False) # 🌟 ADD THIS LINE HERE!

    # Official Database Storage Slots for Student Data
    full_name = db.Column(db.String(100), nullable=True)
    cgpa = db.Column(db.Float, default=0.0)
    stream = db.Column(db.String(50), default='Data Science')

# 2. Detailed Company Profiles
class CompanyProfile(db.Model):
    __tablename__ = 'company_profiles'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False) # Link to the User who created this company
    name = db.Column(db.String(100), unique=True, nullable=False)
    industry = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(20), default='pending', nullable=False) # can be pending, approved, or rejected
    is_blacklisted = db.Column(db.Boolean, default=False)
    
    # Relationships
    drives = db.relationship('PlacementDrive', backref='company', lazy=True)
    
# 3. Placement Drives Created by Companies
class PlacementDrive(db.Model):
    __tablename__ = 'placement_drives'
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer,db.ForeignKey('company_profiles.id'), nullable=False)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    ctc = db.Column(db.String(50), nullable=False)
    min_cgpa = db.Column(db.Float, default=0.0)
    deadline = db.Column(db.String(50), nullable=False) # Format: YYYY-MM-DD
    status = db.Column(db.String(20), default='Pending') # Pending, Approved or Rejected
    
    # 🌟 CHANGE 1: MAP THE NEW DB COLUMN TO ACCUMULATE TARGET STREAMS
    allowed_stream = db.Column(db.Text, default='All Streams')
    
    def to_dict(self):
        return{
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "ctc": self.ctc,
            "min_cgpa": self.min_cgpa,
            "deadline": self.deadline,
            "status": self.status,
            # 🌟 CHANGE 2: INCLUDE IT IN THE JSON OBJECT SENT TO THE FRONTEND
            "allowed_stream": self.allowed_stream
        }

# 4. Applications Table Mapping Students to Drives
class Application(db.Model):
    __tablename__ = 'applications'
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, nullable=False) # Will link to StudentProfile later
    drive_id = db.Column(db.Integer, db.ForeignKey('placement_drives.id'), nullable=False)
    status = db.Column(db.String(30), default='Applied') # 'Applied, 'Shortlisted, 'Selected, 'Rejected'
    applied_date = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Enforce that a student can only apply ONCE per placement drive
    __table_args__ = (db.UniqueConstraint('student_id', 'drive_id', name='_student_drive_uc'),)
    


    
# --- Routes ---

# This route serves as Vue UI entry point. It renders the index.html file from the frontend directory.
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')

    user = User.query.filter_by(username=username).first()
    
    # 🔍 TEMPORARY LIVE DIAGNOSTIC PRINTS
    print("\n=== 🕵️‍♂️ LIVE LOGIN STRING INSPECTION ===")
    print(f"-> Frontend Sent Username: '{username}'")
    print(f"-> Frontend Sent Password: '{password}'")
    if user:
        print(f"-> DB Match Found! Username in Row: '{user.username}'")
        print(f"-> Evaluation Result: {check_password_hash(user.password_hash, password)}")
    else:
        print("-> DB Match Found: ZERO (No user exists with this username string)")
    print("========================================\n")

    # Verify credentials first
    if user and check_password_hash(user.password_hash, password):
        
        # 🛑 UPGRADED MULTI-LAYER DEACTIVATION GUARDRAIL
        is_blocked = getattr(user, 'is_blacklisted', False)
        
        # Fallback Check: Direct lookup without the self-import context landmine
        if not is_blocked and user.role == 'company':
            profile = CompanyProfile.query.filter_by(user_id=user.id).first()
            if profile and getattr(profile, 'is_blacklisted', False):
                is_blocked = True

        print(f"--> [SECURITY CHECK] User: {user.username} | Deactivated: {is_blocked}")

        if is_blocked:
            return jsonify({
                "message": "Access Denied. This account has been deactivated by campus administration."
            }), 403

        # Generate digital wristband token if account is verified and safe
        access_token = create_access_token(identity=user.username)
        return jsonify({'message': 'Login successful', 'token': access_token, 'role': user.role}), 200
    else:
        return jsonify({"msg": "Invalid username or password"}), 401

@app.route('/api/register', methods=['POST'])
def register_user():
    data = request.get_json() or {}
    username = data.get('username')
    password = data.get('password')
    role = data.get('role') # Expecting either 'student' or 'company'
    
    # 1. Validation check
    if not username or not password or not role:
        return jsonify({"message": "Please fill out all registration fields."}), 400
    
    if role not in ['student', 'company']:
        return jsonify({"message": "Invalid role choice selection."}), 400
    
    # 2. Check if the username is already claimed
    user_exists = User.query.filter_by(username=username).first()
    if user_exists:
        return jsonify({"message": "That username is already taken. Try another!"}), 400
    
    # 3. Create and save the new user account
    hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
    
    # Instantiate the new user base record
    new_user = User(username=username, password_hash=hashed_password, role=role)
    
    # 🌟 UPDATED: Direct, aggressive assignment for student academic metrics
    if role == 'student':
        new_user.full_name = data.get('full_name', '')
        try:
            # Safely cast the incoming CGPA string to a float decimal
            new_user.cgpa = float(data.get('cgpa', 0.0)) if data.get('cgpa') else 0.0
        except (ValueError, TypeError):
            new_user.cgpa = 0.0
        new_user.stream = data.get('stream', 'Data Science')
            
    db.session.add(new_user)
    db.session.commit()
    
    # 4. EDGE CASE INSURANCE: Initialize blank CompanyProfile for recruiters
    if role == 'company':
        new_profile = CompanyProfile(
            user_id=new_user.id,
            name=username.replace('_user', '').capitalize(), # Clean default name string
            industry="Technology",
            status="Pending" # Must be cleared by an admin before they can post drives!
        )
        db.session.add(new_profile)
        db.session.commit()
        
    return jsonify({"message": f"Success! Account created for {username} as a {role}."}), 201
    


# --- Protected Admin Route ---
@app.route('/api/admin/dashboard', methods=['GET'])
@jwt_required() # This decorator ensures that only authenticated users can access this route.
def admin_dashboard():
    # Read the token to see who is asking for access to the admin dashboard
    current_user_username = get_jwt_identity() # This retrieves the identity of the currently logged-in user from the JWT token.
    
    # --- LIVE DIAGNOSTIC PRINTS ---
    print("\n=== LIVE DASHBOARD HIT ===")
    print(f"Token identity username is: '{current_user_username}' ")
    
    # Let's print out EVERY single user saved in the database right now
    all_users = User.query.all()
    print(f"Total users inside live DB file: {len(all_users)}")
    for u in all_users:
        print(f"-> DB Entry - Username: '{u.username}' | Role: '{u.role}' ")
    
    # Look up the user in the database to see their role
    user = User.query.filter_by(username=current_user_username).first()
    print(f"Database lookup result found user obj: {user}")
    if user:
        print(f"Does role '{user.role}' == 'admin'? {user.role == 'admin'} ")
    print("===========================\n")
    if not user or user.role != 'admin':
        return jsonify({"message": "Access restricted to admins only."}), 403
    
    return jsonify({"message": f"Welcome to the secret Admin Dashboard, {user.username}!"}), 200

# Route to fetch all companies (for admin dashboard)
@app.route('/api/admin/companies', methods=['GET'])
@jwt_required()
def get_companies():
    current_user_username = get_jwt_identity()
    user = User.query.filter_by(username=current_user_username).first()
    
    if not user or user.role != 'admin':
        return jsonify({"message": "Access restricted to admins only."}), 403
    
    companies = CompanyProfile.query.all()
    company_list = []
    for company in companies:
        company_list.append({
            'id': company.id,
            'name': company.name,
            'industry': company.industry,
            'status': company.status,
            'user_id': company.user_id,
            'is_blacklisted': company.is_blacklisted  # 🌟 ADDED: Keeps the UI state synced on refresh!
        })
    
    return jsonify({'companies': company_list}), 200

# Route to approve a company (for admin dashboard)
@app.route('/api/admin/companies/<int:company_id>/status', methods=['POST'])
@jwt_required()
def update_company_status(company_id):
    current_user_username = get_jwt_identity()
    user = User.query.filter_by(username=current_user_username).first()
    
    if not user or user.role != 'admin':
        return jsonify({"message": "Access restricted to admins only."}), 403
    
    data = request.get_json()
    new_status = data.get('status') # Expecting 'approved' or 'rejected'
    
    company = CompanyProfile.query.get(company_id)
    if not company:
        return jsonify({"message": "Company not found."}), 404
    
    company.status = new_status
    db.session.commit()
    return jsonify({"message": f"Company '{company.name}' status updated to '{new_status}'."}), 200

# --- NEW: Company Self-Registration Route --- 
@app.route('/api/register/company', methods=['POST'])
def register_company():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    company_name = data.get('name')
    industry = data.get('industry')
    
    if not all([username, password, company_name, industry]):
        return jsonify({"message": "All registration fields are required."}), 400
    
    if User.query.filter_by(username=username).first():
        return jsonify({"message": "Username already exists."}), 400
    
    # 1. Create Login User
    new_user = User(
        username=username,
        password_hash=generate_password_hash(password),
        role='company'
    )
    db.session.add(new_user)
    db.session.flush() # Grab the new_user.id before committing
    
    # 2. Create Profile linked to User
    new_profile = CompanyProfile(
        user_id =new_user.id,
        name=company_name,
        industry=industry,
        status='Pending' # Admins must approve this later
    )
    db.session.add(new_profile)
    db.session.commit()

    return jsonify({"message": "Company registered successfully! Awaiting Admin approval."}), 201

# --- NEW: Fetch Company Dashboard Profile and Custom Drives ---
@app.route('/api/company/dashboard', methods=['GET'])
def get_company_dashboard():
    
    from flask import request
    print("--> RAW HEADER VALUE ON SERVER:", request.headers.get('Authorization'))
    
    try:
        verify_jwt_in_request()
        current_username = get_jwt_identity()
    except Exception as e:
        print("--> CRITICAL JWT ERROR DIAGNOSTIC:", str(e))
        return jsonify({"message": "Session verification failed. Please log in again."}), 401   

    user = User.query.filter_by(username=current_username).first()
    
    if not user or user.role != 'company':
        return jsonify({"message": "Access denied. Company role required."}), 403
    
    profile = CompanyProfile.query.filter_by(user_id=user.id).first()
    if not profile:
        return jsonify({"messsage", "Company profile not found."}), 404
    
    # Get all drives posted by this specific company profile
    drives = PlacementDrive.query.filter_by(company_id=profile.id).all()
    
    return jsonify({
        "profile": {
            "name": profile.name,
            "industry": profile.industry,
            "status": profile.status,
            "is_blacklisted": profile.is_blacklisted
        },
        "drives": [drive.to_dict() for drive in drives]
    }), 200
    
@app.route('/api/company/drives', methods=['POST'])
def create_placement_drive():
    try:
        verify_jwt_in_request()
        current_username = get_jwt_identity()
    except Exception as e:
        return jsonify({"message": "Session verification failed."}), 401
    
    user = User.query.filter_by(username=current_username).first()
    if not user or user.role != 'company':
        return jsonify({"message": "Access denied!"}), 403
    
    profile = CompanyProfile.query.filter_by(user_id=user.id).first()
    
    if profile.status != 'Approved':
        return jsonify({"message": "Action barred. Your profile status is still:"+ profile.status}), 403
    
    if profile.is_blacklisted:
        return jsonify({"message": "This account has been deactivated by administration."}), 403
    
    data = request.get_json()
    title = data.get('title')
    description = data.get('description')
    ctc = data.get('ctc')
    min_cgpa = data.get('min_cgpa', 0.0)
    deadline = data.get('deadline')
    allowed_stream = data.get('allowed_stream', 'All Streams')
    
    if not all([title, description, ctc, deadline]):
        return jsonify({"message": "Missing required drive fields."}), 400
    
    # 🌟 FORCE STATUS TO PENDING SO THE ADMIN HAS TO APPROVE IT
    new_drive = PlacementDrive(
        company_id=profile.id,
        title=title,
        description=description,
        ctc=ctc,
        min_cgpa=float(min_cgpa),
        deadline=deadline,
        allowed_stream=allowed_stream,
        status='Pending' 
    )
    
    db.session.add(new_drive)
    db.session.commit()
    
    return jsonify({"message": "Placement drive draft submitted successfully to admin!"}), 200

@app.route('/api/admin/drives', methods=['GET'], endpoint='admin_get_all_drives')
@jwt_required()
def get_all_drives():
    current_user_username = get_jwt_identity()
    user = User.query.filter_by(username=current_user_username).first()
    
    if not user or user.role != 'admin':
        return jsonify({"message": "Access restricted to admins only."}), 403
    
    # Query all drives from the database
    all_drives = PlacementDrive.query.all()
    
    output = []
    for drive in all_drives:
        # Look up the company profile name so the admin knows who posted it
        company = CompanyProfile.query.get(drive.company_id)
        company_name = company.name if company else "Unknown Company"
        
        output.append({
            "id": drive.id,
            "company_name": company_name,
            "title": drive.title,
            "description": drive.description,
            "ctc": drive.ctc,
            "deadline": drive.deadline,
            "status": drive.status
        })
        
    return jsonify(output), 200

@app.route('/api/admin/drives/<int:drive_id>/status', methods=['POST'], endpoint='admin_update_drive_status')
@jwt_required()
def update_drive_status(drive_id):
    current_user_username = get_jwt_identity()
    user = User.query.filter_by(username=current_user_username).first()
    
    if not user or user.role != 'admin':
        return jsonify({"message": "Access restricted to admins only."}), 403
        
    data = request.get_json() or {}
    new_status = data.get('status') 
    
    # 🔍 LIVE INSPECTION LOG
    print(f"=== ⚙️ ADMIN ACTION DETECTED ===")
    print(f"-> Target Drive ID: {drive_id}")
    print(f"-> Payload Status Value Received: '{new_status}'")
    print(f"=================================")
    
    if not new_status:
        return jsonify({"message": "Missing status value in payload."}), 400
        
    drive = PlacementDrive.query.get(drive_id)
    if not drive:
        return jsonify({"message": "Placement drive not found."}), 404
        
    drive.status = new_status
    db.session.commit()
    
    return jsonify({"message": f"Drive status updated to '{new_status}' cleanly."}), 200

# --- STUDENT PILLAR: FETCH APPROVED PLACEMENT DRIVES ---
@app.route('/api/student/drives', methods=['GET'], endpoint='student_get_active_drives')
@jwt_required()
# @cache.cached(timeout=300)
def get_active_student_drives():
    current_user_username = get_jwt_identity()
    user = User.query.filter_by(username=current_user_username).first()
    
    # Security Guardrail: Ensure the user exists and possesses the 'student' role
    if not user or user.role != 'student':
        return jsonify({"message": "Access restricted to student accounts only."}), 403
    
    # CRITICAL: We ONLY query drives where the status is exactly 'Approved'
    active_drives = PlacementDrive.query.filter_by(status='Approved').all()
    
    output = []
    for drive in active_drives:
        # Pull the company profile info so the student knows who is hiring
        company = CompanyProfile.query.get(drive.company_id)
        
        # Guard against edge cases where a company profile might be missing or blacklisted
        if company:
            output.append({
                "id": drive.id,
                "company_name": company.name,
                "industry": company.industry,
                "title": drive.title,
                "description": drive.description,
                "ctc": drive.ctc,
                "deadline": drive.deadline,
                "min_cgpa": drive.min_cgpa,                          # 🌟 PASS CUTOFF TO FRONTEND
                "allowed_stream": drive.allowed_stream or 'All Streams' # 🌟 PASS TARGET STREAM TO FRONTEND
            })
            
    return jsonify(output), 200

# --- STUDENT PILLAR: APPLY TO PLACEMENT DRIVE ---
@app.route('/api/student/apply/<int:drive_id>', methods=['POST'])
@jwt_required()
def apply_to_drive(drive_id):
    from datetime import date 
    
    current_username = get_jwt_identity()
    user = User.query.filter_by(username=current_username).first()
    
    if not user or user.role != 'student':
        return jsonify({"message": "Unauthorized. Students only."}), 403
    
    drive = PlacementDrive.query.get(drive_id)
    if not drive:
        return jsonify({"message": "Placement drive not found."}), 404
        
    # =======================================================
    # 🛡️ SHIELD PILLAR 1: TIME DEADLINE ENFORCEMENT
    # =======================================================
    try:
        deadline_date = date.fromisoformat(drive.deadline)
        if date.today() > deadline_date:
            return jsonify({
                "message": f"Application Closed! The deadline for this campaign passed on {drive.deadline}."
            }), 403
    except Exception:
        if date.today().isoformat() > drive.deadline:
            return jsonify({"message": "Application Closed! The deadline has passed."}), 403

    # =======================================================
    # 🛡️ SHIELD PILLAR 2: ACADEMIC STREAM CHECK (UPGRADED & BULLETPROOF)
    # =======================================================
    student_stream = getattr(user, 'stream', 'Data Science').strip().lower()
    allowed_stream = getattr(drive, 'allowed_stream', 'All Streams').strip()
    
    if allowed_stream.lower() != 'all streams' and allowed_stream.lower() != student_stream:
        return jsonify({
            "message": f"Application Blocked! This position is restricted to {allowed_stream} majors. Your profile is registered under {user.stream}."
        }), 403

    # =======================================================
    # 🛡️ SHIELD PILLAR 3: CGPA CUTOFF CHECK
    # =======================================================
    student_cgpa = getattr(user, 'cgpa', 8.0)
    drive_min_cgpa = getattr(drive, 'min_cgpa', 7.5)
    
    if student_cgpa < drive_min_cgpa:
        return jsonify({
            "message": f"Application Blocked! Requires a minimum CGPA of {drive_min_cgpa}. Your recorded CGPA is {student_cgpa}."
        }), 403

    # Check for duplicate submissions
    existing_app = Application.query.filter_by(student_id=user.id, drive_id=drive_id).first()
    if existing_app:
        return jsonify({"message": "You have already submitted an application for this drive!"}), 400
    
    try:
        new_application = Application(student_id=user.id, drive_id=drive_id, status='Applied')
        db.session.add(new_application)
        db.session.commit()
        return jsonify({"message": f"Success! Application for {drive.title} submitted."}), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"message": f"Database error: {str(e)}"}), 500

@app.route('/api/company/applications', methods=['GET'])
@jwt_required()
def get_company_applications():
    current_username = get_jwt_identity()
    
    # 1. Verify the user exists and is a corporate account
    user = User.query.filter_by(username=current_username).first()
    if not user or user.role != 'company':
        return jsonify({"message": "Unauthorized. Corporate partners only."}), 403
    
    
    # 2. Grab the recruiter's specific Company Profile record
    company_profile = CompanyProfile.query.filter_by(user_id=user.id).first()
    if not company_profile:
        return jsonify({"message": "Company profile configuration missing."}), 404
    
    # 3. Query the DB: Fetch all applications where the drive belongs to THIS company
    # We join Application -> PlacementDrive to cleanly filter by company_id
    results = db.session.query(Application)\
        .join(PlacementDrive)\
        .filter(PlacementDrive.company_id == company_profile.id)\
        .order_by(Application.applied_date.desc())\
        .all()
        
    # 4. Serialize the records into a clean JSON layout for the Vue dashboard
    applications_list = []
    for app_record in results:
        # Fetch the applicant's user record to display their name
        applicant = User.query.get(app_record.student_id)
        drive_details = PlacementDrive.query.get(app_record.drive_id)
        
        applications_list.append({
            "id": app_record.id,
            "student_username": applicant.username if applicant else "Unknown Student",
            "role_title": drive_details.title if drive_details else "Unknown Position",
            "status": app_record.status,
            "applied_date": app_record.applied_date.strftime('%Y-%m-%d %H:%M') if app_record.applied_date else "N/A"
        })
    
    return jsonify({"applications": applications_list}), 200

@app.route('/api/company/applications/<int:app_id>/status', methods=['POST'])
@jwt_required()
def update_application_status(app_id):
    current_username = get_jwt_identity()
    
    # 1. Verify user exists and is a corporate account
    user = User.query.filter_by(username=current_username).first()
    if not user or user.role != 'company':
        return jsonify({"message": "Unauthorized. Corporate partners only."}), 403
    
    # 2. Extract the new target status from the frontend request body
    data = request.get_json() or {}
    new_status = data.get('status') # Expecting 'Shortlisted' or 'Rejected'
    
    if new_status not in ['Shortlisted', 'Rejected', 'Selected', 'Applied']:
        return jsonify({"message": "Invalid status update value."}), 400
    
    # 3. Fetch the specific application record
    application = Application.query.get(app_id)
    if not application:
        return jsonify({"message": "Application record not found."}), 404
    
    # 4. Safety Check: Verify this application actually belongs to a drive owned by THIS company
    company_profile = CompanyProfile.query.filter_by(user_id=user.id).first()
    drive = PlacementDrive.query.get(application.drive_id)
    
    if not company_profile or not drive or drive.company_id != company_profile.id:
        return jsonify({"message": "Unauthorized. You do not manage this placement drive."}), 403
    
    try:
        # 5. Flip the status switch and commit to the database!
        application.status = new_status
        db.session.commit()
        
        return jsonify({
            "message": f"Application status successfully updated to {new_status}!"
        }), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"message": f"Database update error: {str(e)}"}), 500
    
@app.route('/api/student/applications', methods=['GET'])
@jwt_required()
def get_student_applications():
    current_username = get_jwt_identity()
    
    # 1. Fetch the student user record
    user = User.query.filter_by(username=current_username).first()
    if not user or user.role != 'student':
        return jsonify({"message": "Unauthorized. Students only."}), 403
        
    # 2. Grab all applications submitted by this specific student
    apps = Application.query.filter_by(student_id=user.id).all()
    
    # 3. Serialize data and inject the dummy interview link if they are shortlisted
    serialized_apps = []
    for a in apps:
        drive = PlacementDrive.query.get(a.drive_id)
        company_name = "Corporate Partner"
        
        if drive:
            # Safely fetch company profile record
            comp_profile = CompanyProfile.query.get(drive.company_id)
            if comp_profile:
                # 🌟 BULLETPROOF FALLBACK: Checks for .name, then falls back to User table username
                company_name = getattr(comp_profile, 'name', None)
                if not company_name:
                    comp_user = User.query.get(comp_profile.user_id)
                    company_name = comp_user.username if comp_user else "Corporate Partner"

        app_data = {
            "id": a.id,
            "drive_title": drive.title if drive else "Unknown Position",
            "company_name": company_name,
            "status": a.status,
            "interview_link": None
        }
        
        # 🌟 YOUR DUMMY MEET LINK: Exposed instantly upon shortlisting!
        if a.status == 'Shortlisted':
            app_data["interview_link"] = "https://meet.google.com/abc-defg-hij"
            
        serialized_apps.append(app_data)
        
    return jsonify({"my_applications": serialized_apps}), 200
    
@app.route('/api/student/profile/update', methods=['POST'])
@jwt_required()
def update_student_profile():
    current_username = get_jwt_identity()
    user = User.query.filter_by(username=current_username).first()
    
    if not user or user.role != 'student':
        return jsonify({"message": "Unauthorized user token."}), 403
        
    data = request.get_json() or {}
    
    # 1. Capture incoming fields from frontend inputs
    new_name = data.get('full_name')
    new_cgpa = data.get('cgpa')
    new_stream = data.get('stream')
    
    print(f"\n=== 📥 INCOMING PROFILE UPDATE FOR: {current_username} ===")
    print(f"-> Received Name: {new_name}, CGPA: {new_cgpa}, Stream: {new_stream}")
    
    try:
        # 2. Force direct assignments to expose any silent column schema mismatches
        user.full_name = new_name
        user.cgpa = float(new_cgpa) if new_cgpa else 0.0
        user.stream = new_stream
            
        db.session.commit()
        print("✅ DATABASE TRANSACTION COMMITTED SUCCESSFULLY!")
        
        return jsonify({
            "message": "Account details successfully saved to database!",
            "full_name": user.full_name,
            "cgpa": user.cgpa,
            "stream": user.stream
        }), 200
        
    except ValueError:
        print("❌ VALUE ERROR: Invalid CGPA format.")
        return jsonify({"message": "Invalid CGPA format. Must be a numeric decimal."}), 400
    except Exception as e:
        db.session.rollback()
        print(f"❌ DATABASE TRANSACTION CRASHED: {str(e)}")
        return jsonify({"message": f"Error updating account: {str(e)}"}), 500
    
@app.route('/api/company/drive/<int:drive_id>', methods=['DELETE'])
@jwt_required()
def delete_drive_backend(drive_id):
    current_username = get_jwt_identity()
    
    # 1. Verify user exists and is a corporate account
    user = User.query.filter_by(username=current_username).first()
    if not user or user.role != 'company':
        return jsonify({"message": "Unauthorized. Corporate accounts only."}), 403

    # 2. Fetch the target placement campaign
    drive = PlacementDrive.query.get(drive_id)
    if not drive:
        return jsonify({"message": "Placement drive campaign record not found."}), 404

    try:
        # 3. Clean up database dependencies: wipe any student applications for this drive first
        Application.query.filter_by(drive_id=drive_id).delete()
        
        # 4. Evict the drive campaign from the table and commit changes
        db.session.delete(drive)
        db.session.commit()
        
        return jsonify({"message": "Placement drive campaign successfully deleted!"}), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"message": f"Database execution error: {str(e)}"}), 500
    
@app.route('/api/company/drive/<int:drive_id>', methods=['PUT'])
@jwt_required()
def update_placement_drive(drive_id):
    current_username = get_jwt_identity()
    user = User.query.filter_by(username=current_username).first()
    
    if not user or user.role != 'company':
        return jsonify({"message": "Unauthorized. Corporate accounts only."}), 403

    drive = PlacementDrive.query.get(drive_id)
    if not drive:
        return jsonify({"message": "Recruitment campaign not found."}), 404

    # Security Check: Ensure this company owns the campaign
    company_profile = CompanyProfile.query.filter_by(user_id=user.id).first()
    if not company_profile or drive.company_id != company_profile.id:
        return jsonify({"message": "Unauthorized to modify this campaign."}), 403

    data = request.get_json() or {}
    
    # Update core attributes dynamically
    drive.title = data.get('title', drive.title)
    drive.description = data.get('description', drive.description)
    drive.ctc = data.get('ctc', drive.ctc)
    drive.deadline = data.get('deadline', drive.deadline)
    
    # Safely update min_cgpa requirement if column exists
    if 'min_cgpa' in data and hasattr(drive, 'min_cgpa'):
        try:
            drive.min_cgpa = float(data.get('min_cgpa'))
        except (ValueError, TypeError):
            pass

    try:
        db.session.commit()
        return jsonify({"message": "Placement campaign updated successfully!"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"message": f"Database write failure: {str(e)}"}), 500
    
@app.route('/api/student/resume/upload', methods=['POST'])
@jwt_required()
def upload_resume():
    current_username = get_jwt_identity()
    user = User.query.filter_by(username=current_username).first()
    
    if not user or user.role != 'student':
        return jsonify({"message": "Unauthorized. Students only."}), 403
        
    if 'resume' not in request.files:
        return jsonify({"message": "No file stream detected in request."}), 400
        
    file = request.files['resume']
    if file.filename == '':
        return jsonify({"message": "No file selected."}), 400
        
    # Security enforcement: Only allow official PDF files
    if not file.filename.lower().endswith('.pdf'):
        return jsonify({"message": "Invalid file format. Only PDF documents are permitted."}), 400

    try:
        # Deterministic filename keeps our database layout completely clean!
        filename = f"resume_student_{user.id}.pdf"
        file_path = os.path.join(UPLOAD_FOLDER, filename)
        
        # Overwrites any old resume file instantly if they upload a new version
        file.save(file_path)
        return jsonify({"message": "Resume document uploaded and synchronized successfully!"}), 200
    except Exception as e:
        return jsonify({"message": f"File system save failure: {str(e)}"}), 500


@app.route('/api/student/resume/view/<int:student_id>', methods=['GET'])
@jwt_required()
def view_resume(student_id):
    # This endpoint allows both the student and corporate recruiters to view the document
    filename = f"resume_student_{student_id}.pdf"
    
    if not os.path.exists(os.path.join(UPLOAD_FOLDER, filename)):
        return jsonify({"message": "No resume uploaded yet for this profile."}), 404
        
    return send_from_directory(UPLOAD_FOLDER, filename, mimetype='application/pdf')

@app.route('/api/student/profile', methods=['GET'])
@jwt_required()
def get_student_profile():
    current_username = get_jwt_identity()
    user = User.query.filter_by(username=current_username).first()
    
    if not user or user.role != 'student':
        return jsonify({"message": "Unauthorized user token."}), 403
        
    # Build the payload explicitly
    payload = {
        "full_name": getattr(user, 'full_name', ''),
        "cgpa": getattr(user, 'cgpa', 0.0),
        "stream": getattr(user, 'stream', 'Data Science')
    }
    
    # 🌟 DIAGNOSTIC PRINT: Check what the backend is about to send
    print(f"\n=== 📤 SHIPPING OUT PROFILE DATA FOR: {current_username} ===")
    print(f"-> Sending payload data: {payload}")
    
    return jsonify(payload), 200


# =====================================================================
# 🚀 BACKGROUND JOBS ENGINE (CELERY TASKS & ROUTING ENDPOINTS)
# =====================================================================

# 🏢 JOB 1: COMPANY-TRIGGERED ASYNC EXPORT (APPLICANTS LIST)
@celery.task(name="app.export_applicants_csv")
def export_applicants_csv(company_id, role_title):
    import os
    import csv
    from app import Application 
    
    # 🌟 FIXED: Use absolute file path mapping relative to app.py
    base_dir = os.path.dirname(os.path.abspath(__file__))
    export_dir = os.path.join(base_dir, 'static', 'exports')
    os.makedirs(export_dir, exist_ok=True)
    
    filename = f"applicants_{company_id}_{role_title.replace(' ', '_')}.csv"
    filepath = os.path.join(export_dir, filename)
    
    records = Application.query.filter_by(role_title=role_title).all()
    
    with open(filepath, mode='w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerow(['Application ID', 'Student Username', 'Current Status', 'Applied Date'])
        for app_record in records:
            writer.writerow([
                app_record.id, 
                getattr(app_record, 'student_username', 'N/A'), 
                app_record.status, 
                getattr(app_record, 'applied_date', 'N/A')
            ])
    return {"status": "Success", "download_url": f"/static/exports/{filename}"}

@app.route('/api/company/export-csv', methods=['POST'])
@jwt_required()
def trigger_company_export(): 
    data = request.get_json() or {}
    role_title = data.get('role_title')
    if not role_title:
        return jsonify({"message": "Role title target is required."}), 400
    task = export_applicants_csv.delay(company_id=1, role_title=role_title)
    return jsonify({"message": "Background export worker initiated successfully!", "task_id": task.id}), 202


# 🎓 JOB 2: STUDENT-TRIGGERED ASYNC EXPORT (PERSONAL HISTORY - REQ C)
@celery.task(name="app.export_student_history_csv")
def export_student_history_csv(student_id, username):
    import os
    import csv
    from app import Application
    
    # 🌟 FIXED: Use absolute file path mapping relative to app.py
    base_dir = os.path.dirname(os.path.abspath(__file__))
    export_dir = os.path.join(base_dir, 'static', 'exports')
    os.makedirs(export_dir, exist_ok=True)
    
    filename = f"student_history_{username}_{student_id}.csv"
    filepath = os.path.join(export_dir, filename)
    
    records = Application.query.filter_by(student_id=student_id).all()
    
    with open(filepath, mode='w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerow(['Student ID', 'Company Name', 'Drive Title', 'Application Status', 'Applied Date'])
        for app_record in records:
            writer.writerow([
                student_id,
                getattr(app_record, 'company_name', 'Corporate Partner'),
                getattr(app_record, 'role_title', 'N/A'),
                app_record.status,
                getattr(app_record, 'applied_date', 'N/A')
            ])
    return {"status": "Success", "download_url": f"/static/exports/{filename}"}

@app.route('/api/student/export-history', methods=['POST'])
@jwt_required()
def export_history():
    current_user = get_jwt_identity()
    user = User.query.filter_by(username=current_user).first()
    
    if not user or user.role != 'student':
        return jsonify({"message": "Unauthorized access."}), 403

    def generate_and_email_csv(user_id, username):
        with app.app_context():
            # 1. Gather Application History Rows
            apps = Application.query.filter_by(student_id=user_id).all()
            
            os.makedirs('static/exports', exist_ok=True)
            filepath = f"static/exports/applications_{username}.csv"
            filename = f"applications_{username}.csv"
            
            with open(filepath, mode='w', newline='', encoding='utf-8') as file:
                writer = csv.writer(file)
                writer.writerow(['Student ID', 'Company Name', 'Drive Title', 'Application Status', 'Applied Date'])
                
                for app_record in apps:
                    drive = PlacementDrive.query.get(app_record.drive_id)
                    company_name = drive.company.name if (drive and drive.company) else "N/A"
                    drive_title = drive.title if drive else "N/A"
                    date_str = app_record.applied_date.strftime('%Y-%m-%d %H:%M:%S') if app_record.applied_date else 'N/A'
                    
                    writer.writerow([user_id, company_name, drive_title, app_record.status, date_str])
            
            print(f"--> [BATCH SUCCESS] CSV generated locally for {username}. Initializing email transfer...")

            # 2. Construct the Explicit Multipart Mixed Envelope
            sender_email = "placementcell@iitm.ac.in"
            receiver_email = f"{username}@student.iitm.ac.in"
            
            msg = MIMEMultipart('mixed')
            msg['Subject'] = "🎓 Institutional Placement Portal - Your Application History Export"
            msg['From'] = sender_email
            msg['To'] = receiver_email
            
            body = f"Hello {username},\n\nYour requested batch export job has finished compiling successfully. Please find your complete campus recruitment application history attached below as a clean CSV file.\n\nBest regards,\nCampus Placement Cell Office"
            msg.attach(MIMEText(body, 'plain'))
            
            # 3. Stream and Bind Using text/csv Classifier
            try:
                with open(filepath, "rb") as attachment:
                    part = MIMEBase("text", "csv")
                    part.set_payload(attachment.read())
                    
                encoders.encode_base64(part)
                part.add_header('Content-Disposition', 'attachment', filename=filename)
                msg.attach(part)
                
                # 4. Slam the Data Pipeline Straight Into Mailhog's Local SMTP Server Port
                with smtplib.SMTP('localhost', 1025) as server:
                    # 🌟 FIX INSULATED HERE: Using native network standard transmission
                    server.send_message(msg)
                    
                print(f"--> [MAILHOG SUCCESS] Async email dispatch intercept confirmed for: {username}")
            except Exception as mail_error:
                print(f"--> [MAIL ERROR] Background transmission hit a snag: {str(mail_error)}")

    # 🚀 Launch worker thread immediately so the student dashboard never experiences lag
    threading.Thread(target=generate_and_email_csv, args=(user.id, user.username)).start()

    return jsonify({
        "message": "Batch export job initialized. Your data payload is compiling and will arrive via email shortly.",
        "task_id": f"EMAIL-EXTRACT-{user.id}"
    }), 202


# --- ADMIN PILLAR: FETCH METRIC COUNT STATISTICS ---
@app.route('/api/admin/stats', methods=['GET'])
@jwt_required()
def get_admin_stats():
    current_user_username = get_jwt_identity()
    user = User.query.filter_by(username=current_user_username).first()
    
    # Security Check
    if not user or user.role != 'admin':
        return jsonify({"message": "Access restricted to admins only."}), 403
        
    try:
        # Count rows in database tables based on roles and statuses
        total_students = User.query.filter_by(role='student').count()
        total_companies = CompanyProfile.query.count()
        total_drives = PlacementDrive.query.count()
        
        return jsonify({
            "total_students": total_students,
            "total_companies": total_companies,
            "total_drives": total_drives
        }), 200
    except Exception as e:
        return jsonify({"message": f"Failed to gather statistics: {str(e)}"}), 500
    
# --- ADMIN PILLAR: VIEW ALL REGISTERED STUDENTS ---
@app.route('/api/admin/students', methods=['GET'])
@jwt_required()
def get_all_students_for_admin():
    current_user_username = get_jwt_identity()
    user = User.query.filter_by(username=current_user_username).first()
    
    # Access Control Guardrail
    if not user or user.role != 'admin':
        return jsonify({"message": "Access restricted to admins only."}), 403
        
    try:
        # Pull all rows where the role is exactly 'student'
        students = User.query.filter_by(role='student').all()
        
        output = []
        for student in students:
            # 🌟 FIXED: Direct reading straight from the student user object rows!
            output.append({
                "id": student.id,
                "username": student.username,
                "name": student.full_name if student.full_name else 'Not Onboarded Yet',
                "cgpa": student.cgpa if student.cgpa else 0.0,
                "stream": student.stream if student.stream else 'Data Science',
                "is_blacklisted": getattr(student, 'is_blacklisted', False)
            })
            
        return jsonify(output), 200
    except Exception as e:
        return jsonify({"message": f"Failed to retrieve students roster: {str(e)}"}), 500
    
# --- ADMIN PILLAR: TOGGLE COMPANY DEACTIVATION ---
@app.route('/api/admin/companies/<int:company_id>/toggle-blacklist', methods=['POST'])
@jwt_required()
def toggle_company_blacklist(company_id):
    current_user = get_jwt_identity()
    admin = User.query.filter_by(username=current_user).first()
    if not admin or admin.role != 'admin':
        return jsonify({"message": "Access restricted to admins."}), 403
        
    company_profile = CompanyProfile.query.get(company_id)
    if not company_profile:
        return jsonify({"message": "Company profile not found."}), 404
        
    user = User.query.get(company_profile.user_id)
    
    try:
        # Flip the status for both the profile and the auth user row
        new_status = not company_profile.is_blacklisted
        company_profile.is_blacklisted = new_status
        if user:
            user.is_blacklisted = new_status
            
        db.session.commit()
        action = "deactivated" if new_status else "reactivated"
        return jsonify({"message": f"Company '{company_profile.name}' successfully {action}."}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"message": f"Error: {str(e)}"}), 500

# --- ADMIN PILLAR: TOGGLE STUDENT DEACTIVATION ---
@app.route('/api/admin/students/<int:user_id>/toggle-blacklist', methods=['POST'])
@jwt_required()
def toggle_student_blacklist(user_id):
    current_user = get_jwt_identity()
    admin = User.query.filter_by(username=current_user).first()
    if not admin or admin.role != 'admin':
        return jsonify({"message": "Access restricted to admins."}), 403
        
    student_user = User.query.get(user_id)
    if not student_user or student_user.role != 'student':
        return jsonify({"message": "Student account not found."}), 404
        
    try:
        student_user.is_blacklisted = not student_user.is_blacklisted
        db.session.commit()
        action = "deactivated" if student_user.is_blacklisted else "reactivated"
        return jsonify({"message": f"Student '{student_user.username}' successfully {action}."}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"message": f"Error: {str(e)}"}), 500

#  --- Run the Flask application ---
if __name__ == '__main__':
    with app.app_context():
    # 1. Tells SQLAlchemy to build all missing tables matching our current models
        db.create_all()
    
        # 2. Checks if an admin account already exists; if not, it builds one instantly
        if not User.query.filter_by(username='admin').first():
            print("--> Seeding fresh, secure admin account into the unified database...")
            admin_password_hashed = generate_password_hash('admin123', method='pbkdf2:sha256')
        
            seeded_admin = User(
                username='admin',
                password_hash=admin_password_hashed,
                role='admin'
            )
            db.session.add(seeded_admin)
            db.session.commit()
            print("--> Admin account successfully seeded!")

    app.run(debug=True)

