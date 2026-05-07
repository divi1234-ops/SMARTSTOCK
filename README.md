# SmartStock Inventory Management System

SmartStock is a Flask-based inventory management system designed to manage products, inventories, users, and transactions efficiently.

## Features

- User authentication (Login/Register)
- Product management
- Inventory tracking
- Transaction management
- Dashboard and reporting
- SQLite database integration
- Flask web interface

## Tech Stack

- Python
- Flask
- SQLite
- HTML/CSS
- Jinja Templates

## Project Structure

```bash
SMARTSTOCK/
│── app.py
│── requirements.txt
│── seed.py
│── migrate_db.py
│── templates/
│── instance/
│── .gitignore
│── README.md
```

## Installation

1. Clone the repository

```bash
git clone https://github.com/your-username/SMARTSTOCK.git
cd SMARTSTOCK
```

2. Create virtual environment

```bash
python -m venv .venv
```

3. Activate environment

### Windows
```bash
.venv\Scripts\activate
```

### Linux/Mac
```bash
source .venv/bin/activate
```

4. Install dependencies

```bash
pip install -r requirements.txt
```

5. Run the application

```bash
python app.py
```

## GitHub Push Commands

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/your-username/SMARTSTOCK.git
git push -u origin main
```

## Author

Diviya Ranawat