# Placement Portal Application 🎓

A full-stack, decoupled web application built to streamline campus placement processes. The platform features role-based workflows for **Students**, **Company Recruiters**, and **Portal Administrators**, powered by a **Flask REST API** backend and a **Vue.js** reactive frontend.

---

## 🚀 Key Features

### 👤 Role-Based Access Control (RBAC)
* **Student Dashboard:** Search placement drives, view eligibility, submit applications, track application status, and export application history asynchronously.
* **Company Portal:** Create placement drives, set job requirements, view candidate lists, and update applicant statuses.
* **Admin Console:** Manage user approvals, review drive listings, view aggregate placement statistics, and manage system logs.

### 🛡️ Security & Performance
* **Stateless Authentication:** Secure route protection using **JSON Web Tokens (JWT)** attached to HTTP request headers.
* **Password Security:** Salted password hashing using `werkzeug.security` (`pbkdf2:sha256`).
* **Asynchronous Jobs:** In-process background worker threads for resource-intensive CSV export tasks.
* **Performance Caching:** Server-side response caching via `Flask-Caching` for high-frequency dashboard analytics.

---

## 🛠️ Tech Stack

* **Frontend:** Vue.js 3 (CDN / Reactive Single Page Application), Bootstrap 5, Custom CSS
* **Backend:** Python 3, Flask (RESTful API), Flask-JWT-Extended, Flask-CORS
* **Database & ORM:** SQLite, SQLAlchemy ORM
* **Asynchronous Tasks:** Python `threading` module

---

## 📂 Project Architecture

```text
.
├── app.py              # Flask REST API routes & backend logic
├── models.py           # SQLAlchemy database schema models
├── static/             # Frontend assets (Vue scripts, styles)
│   ├── js/
│   │   └── app.js      # Core Vue.js application logic
│   └── css/
│       └── style.css   # Global application styling
├── templates/          # HTML templates (SPA entry point)
│   └── index.html
├── requirements.txt    # Python dependencies
└── README.md           # Project documentation