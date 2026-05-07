from flask import Flask, render_template, request, redirect, url_for, flash, Response, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import io
import csv
import smtplib
from email.message import EmailMessage
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from functools import wraps
import os
import logging
import time

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import func

# ---------------------- APP CONFIG ----------------------
app = Flask(__name__)
app.secret_key = 'smartstock_secret_key_change_in_production'
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://root:@localhost/smartstock'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Email settings for registration confirmation
app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.environ.get('MAIL_USE_TLS', 'true').lower() in ('true', '1', 'yes')
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER', 'no-reply@smartstock.com')

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'
login_manager.login_message_category = 'info'
failed_attempts = {}
lock_until = {}

# ---------------------- MODELS ----------------------
class AppUser(UserMixin, db.Model):
    """Authentication user model with roles"""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default='staff', nullable=False)  # 'admin' or 'staff'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def set_password(self, password):
        # Use a widely-supported algorithm to avoid platform-specific issues
        # (some environments may not support newer algorithms like scrypt)
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha256')
    
    def check_password(self, password):
        try:
            return check_password_hash(self.password_hash, password)
        except Exception:
            # If the stored hash uses an unsupported algorithm (e.g. scrypt not available
            # in this Python/OpenSSL build) treat it as authentication failure instead of
            # raising a 500 error. The admin can reset the password to rehash with PBKDF2.
            return False
    
    def is_admin(self):
        return self.role == 'admin'


class User(db.Model):
    """Legacy user model for inventory managers"""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    inventories = db.relationship('Inventory', backref='manager', lazy=True)


class Inventory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, index=True)
    location = db.Column(db.String(150), index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    products = db.relationship('Product', backref='inventory', lazy=True)


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, index=True)
    category = db.Column(db.String(100), index=True)
    quantity = db.Column(db.Integer, default=0, nullable=False)
    min_threshold = db.Column(db.Integer, default=5, nullable=False)
    inventory_id = db.Column(db.Integer, db.ForeignKey('inventory.id'), nullable=False)


class Transaction(db.Model):
    """Transaction history with full details"""
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    product = db.relationship('Product', backref=db.backref('transactions', cascade='all, delete-orphan', lazy=True))
    quantity = db.Column(db.Integer, nullable=False)
    action = db.Column(db.String(10), nullable=False)  # 'purchase' or 'use'
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('app_user.id'), nullable=True)
    user = db.relationship('AppUser', backref='transactions')
    username = db.Column(db.String(100))  # Fallback for legacy data
    notes = db.Column(db.Text)  # Optional notes

    def get_user_name(self):
        if self.user:
            return self.user.username
        return self.username or 'Unknown'


@login_manager.user_loader
def load_user(user_id):
    return AppUser.query.get(int(user_id))


# ---------------------- DECORATORS ----------------------
def admin_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin():
            flash('Admin access required.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function


# ---------------------- AUTHENTICATION ROUTES ----------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    ip = request.remote_addr

    # Check if blocked
    if ip in lock_until:
        if time.time() < lock_until[ip]:
            remaining = int(lock_until[ip] - time.time())
            flash(f'Too many login attempts. Try again after {remaining} seconds.', 'danger')
            return render_template('login.html')
        else:
            del lock_until[ip]
            failed_attempts[ip] = 0

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        remember = bool(request.form.get('remember'))

        if not username or not password:
            flash('Username and password are required.', 'danger')
            return render_template('login.html')

        user = AppUser.query.filter_by(username=username).first()

        if user and user.check_password(password):
            failed_attempts[ip] = 0
            login_user(user, remember=remember)
            flash(f'Welcome back, {user.username}!', 'success')
            return redirect(url_for('dashboard'))

        else:
            failed_attempts[ip] = failed_attempts.get(ip, 0) + 1

            if failed_attempts[ip] >= 3:
                lock_until[ip] = time.time() + 30
                failed_attempts[ip] = 0
                flash('Too many attempts. Login blocked for 30 seconds.', 'danger')
            else:
                left = 3 - failed_attempts[ip]
                flash(f'Invalid username or password. {left} attempts left.', 'warning')

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))


@app.route('/register', methods=['GET', 'POST'])
@admin_required
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        role = request.form.get('role', 'staff').strip()
        
        errors = []
        if not username:
            errors.append('Username is required.')
        if not email:
            errors.append('Email is required.')
        if not password or len(password) < 4:
            errors.append('Password must be at least 4 characters.')
        if role not in ['admin', 'staff']:
            errors.append('Invalid role.')
        
        if errors:
            for error in errors:
                flash(error, 'danger')
            return render_template('register.html')
        
        if AppUser.query.filter_by(username=username).first():
            flash('Username already exists.', 'danger')
            return render_template('register.html')
        
        if AppUser.query.filter_by(email=email).first():
            flash('Email already exists.', 'danger')
            return render_template('register.html')
        
        try:
            user = AppUser(username=username, email=email, role=role)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()

            email_body = (
                f'Hello {username},\n\n'
                'Your SmartStock account has been created successfully.\n\n'
                f'Username: {username}\n'
                f'Role: {role.capitalize()}\n\n'
                'You can now log in at the SmartStock portal using the password you provided during registration.\n\n'
                'If you did not request this account, please contact your administrator.\n\n'
                'Best regards,\n'
                'SmartStock Team'
            )
            email_result = send_email('Your SmartStock account is ready', email, email_body)
            if email_result is True:
                flash(f'User {username} registered successfully and email sent.', 'success')
            elif isinstance(email_result, str):
                flash(
                    f'User {username} registered successfully. Confirmation email was saved locally to {email_result}.',
                    'success'
                )
            else:
                if not app.config.get('MAIL_USERNAME') or not app.config.get('MAIL_PASSWORD'):
                    flash(
                        'User registered successfully, but the confirmation email could not be sent because SMTP credentials are not configured. '
                        'Set MAIL_USERNAME and MAIL_PASSWORD in your environment, or check the files in instance/emails for the saved message.',
                        'warning'
                    )
                else:
                    flash(
                        'User registered successfully, but the confirmation email could not be sent. '
                        'Please check your SMTP settings.',
                        'warning'
                    )

            return redirect(url_for('users'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error registering user: {str(e)}', 'danger')
    
    return render_template('register.html')


# ---------------------- HELPER FUNCTIONS ----------------------
def send_email(subject, recipient, body):
    message = EmailMessage()
    message['Subject'] = subject
    message['From'] = app.config['MAIL_DEFAULT_SENDER']
    message['To'] = recipient
    message.set_content(body)

    # If SMTP credentials are missing, save the email locally for review.
    if not app.config.get('MAIL_USERNAME') or not app.config.get('MAIL_PASSWORD'):
        app.logger.warning('Mail credentials are not configured; saving registration email locally.')
        emails_dir = os.path.join(app.instance_path, 'emails')
        os.makedirs(emails_dir, exist_ok=True)
        filename = os.path.join(emails_dir, f'registration_{recipient.replace("@", "_").replace(".", "_")}_{int(datetime.utcnow().timestamp())}.txt')
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(f'To: {recipient}\n')
            f.write(f'Subject: {subject}\n')
            f.write('\n')
            f.write(body)
        return filename

    try:
        with smtplib.SMTP(app.config['MAIL_SERVER'], app.config['MAIL_PORT']) as smtp:
            if app.config['MAIL_USE_TLS']:
                smtp.starttls()
            smtp.login(app.config['MAIL_USERNAME'], app.config['MAIL_PASSWORD'])
            smtp.send_message(message)
        return True
    except Exception as e:
        app.logger.error(f'Failed sending registration email: {e}')
        return False


def get_pagination(request, query, per_page=20):
    """Helper function for pagination"""
    page = request.args.get('page', 1, type=int)
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    return pagination


# ---------------------- ROUTES ----------------------
@app.route('/')
@app.route('/dashboard')
@login_required
def dashboard():
    low_stock = Product.query.filter(Product.quantity <= Product.min_threshold).limit(10).all()
    total_products = Product.query.count()
    total_inventories = Inventory.query.count()
    recent_transactions = Transaction.query.order_by(Transaction.timestamp.desc()).limit(5).all()
    
    return render_template('dashboard.html', 
                         low_stock=low_stock, 
                         total_products=total_products,
                         total_inventories=total_inventories,
                         recent_transactions=recent_transactions)


# ---------------------- USERS (Legacy) ----------------------
@app.route('/users')
@login_required
def users():
    managers_query = User.query.order_by(User.name)
    pagination = get_pagination(request, managers_query, per_page=20)
    registered_users = AppUser.query.order_by(AppUser.username).all()
    return render_template('users.html', pagination=pagination, users=pagination.items, registered_users=registered_users)


@app.route('/delete_manager/<int:id>', methods=['POST'])
@login_required
@admin_required
def delete_manager(id):
    manager = User.query.get_or_404(id)
    try:
        db.session.delete(manager)
        db.session.commit()
        flash(f'Manager "{manager.name}" deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting manager: {str(e)}', 'danger')
    return redirect(url_for('users'))


@app.route('/delete_registered_user/<int:id>', methods=['POST'])
@login_required
@admin_required
def delete_registered_user(id):
    user = AppUser.query.get_or_404(id)
    if user.username == 'admin' or user.id == current_user.id:
        flash('Cannot delete this user account.', 'danger')
        return redirect(url_for('users'))
    try:
        db.session.delete(user)
        db.session.commit()
        flash(f'Registered user "{user.username}" deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting registered user: {str(e)}', 'danger')
    return redirect(url_for('users'))


@app.route('/add_user', methods=['POST'])
@login_required
def add_user():
    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip().lower()
    
    errors = []
    if not name:
        errors.append('Name is required.')
    if not email:
        errors.append('Email is required.')
    elif '@' not in email:
        errors.append('Invalid email format.')
    
    if errors:
        for error in errors:
            flash(error, 'danger')
        return redirect(url_for('users'))
    
    if User.query.filter_by(email=email).first():
        flash('A user with that email already exists.', 'warning')
        return redirect(url_for('users'))
    
    try:
        user = User(name=name, email=email)
        db.session.add(user)
        db.session.commit()
        flash('User added successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error adding user: {str(e)}', 'danger')
    
    return redirect(url_for('users'))


# ---------------------- INVENTORIES ----------------------
@app.route('/inventories')
@login_required
def inventories():
    query = Inventory.query.order_by(Inventory.name)
    pagination = get_pagination(request, query, per_page=20)
    users = User.query.all()
    return render_template('inventories.html', pagination=pagination, inventories=pagination.items, users=users)


@app.route('/add_inventory', methods=['POST'])
@login_required
def add_inventory():
    name = request.form.get('name', '').strip()
    location = request.form.get('location', '').strip()
    user_id_raw = request.form.get('user_id')
    
    errors = []
    if not name:
        errors.append('Inventory name is required.')
    if not user_id_raw:
        errors.append('Manager (user) is required.')
    
    if errors:
        for error in errors:
            flash(error, 'danger')
        return redirect(url_for('inventories'))
    
    try:
        user_id = int(user_id_raw)
    except ValueError:
        flash('Invalid user ID.', 'danger')
        return redirect(url_for('inventories'))
    
    if not User.query.get(user_id):
        flash('Selected manager does not exist.', 'danger')
        return redirect(url_for('inventories'))
    
    try:
        inv = Inventory(name=name, location=location, user_id=user_id)
        db.session.add(inv)
        db.session.commit()
        flash('Inventory created successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error creating inventory: {str(e)}', 'danger')
    
    return redirect(url_for('inventories'))


@app.route('/delete_inventory/<int:id>', methods=['POST'])
@login_required
@admin_required
def delete_inventory(id):
    inventory = Inventory.query.get_or_404(id)
    if inventory.products:
        flash('Cannot delete inventory with products. Remove its products first.', 'warning')
        return redirect(url_for('inventories'))
    try:
        db.session.delete(inventory)
        db.session.commit()
        flash(f'Inventory "{inventory.name}" deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting inventory: {str(e)}', 'danger')
    return redirect(url_for('inventories'))


# ---------------------- PRODUCTS ----------------------
@app.route('/products')
@login_required
def products():
    inventories = Inventory.query.all()
    query = Product.query.join(Inventory, isouter=True)
    
    # --- Search / Filter ---
    q_name = request.args.get('q_name', '').strip()
    q_cat = request.args.get('q_cat', '').strip()
    q_inv = request.args.get('q_inv', '').strip()
    
    if q_name:
        query = query.filter(Product.name.ilike(f"%{q_name}%"))
    if q_cat:
        query = query.filter(Product.category.ilike(f"%{q_cat}%"))
    if q_inv:
        try:
            inv_id = int(q_inv)
            query = query.filter(Product.inventory_id == inv_id)
        except ValueError:
            pass
    
    query = query.order_by(Product.name)
    pagination = get_pagination(request, query, per_page=20)
    return render_template('products.html', pagination=pagination, products=pagination.items, inventories=inventories,
                         q_name=q_name, q_cat=q_cat, q_inv=q_inv)


@app.route('/add_product', methods=['POST'])
@login_required
def add_product():
    name = request.form.get('name', '').strip()
    category = request.form.get('category', '').strip()
    quantity_raw = request.form.get('quantity', '0')
    inv_id_raw = request.form.get('inventory_id')
    
    errors = []
    if not name:
        errors.append('Product name is required.')
    if not inv_id_raw:
        errors.append('Inventory selection is required.')
    
    if errors:
        for error in errors:
            flash(error, 'danger')
        return redirect(url_for('products'))
    
    try:
        quantity = max(0, int(quantity_raw))
        threshold = 5
        inv_id = int(inv_id_raw)
    except ValueError:
        flash('Invalid numeric input. Quantity and threshold must be numbers.', 'danger')
        return redirect(url_for('products'))
    
    if not Inventory.query.get(inv_id):
        flash('Selected inventory does not exist.', 'danger')
        return redirect(url_for('products'))
    
    try:
        product = Product(
            name=name,
            category=category,
            quantity=quantity,
            min_threshold=threshold,
            inventory_id=inv_id
        )
        db.session.add(product)
        db.session.commit()
        flash('Product added successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error adding product: {str(e)}', 'danger')
    
    return redirect(url_for('products'))


# ---------------------- UPDATE STOCK ----------------------
@app.route('/update_stock/<int:id>', methods=['POST'])
@login_required
def update_stock(id):
    product = Product.query.get_or_404(id)
    qty_raw = request.form.get('quantity', '0')
    action = request.form.get('action', '').strip().lower()
    notes = request.form.get('notes', '').strip()
    
    errors = []
    try:
        qty = int(qty_raw)
        if qty <= 0:
            errors.append('Quantity must be greater than 0.')
    except ValueError:
        errors.append('Quantity must be a valid number.')
    
    if action not in ('purchase', 'use'):
        errors.append('Invalid action. Must be "purchase" or "use".')
    
    if errors:
        for error in errors:
            flash(error, 'danger')
        return redirect(url_for('products'))
    
    try:
        if action == 'purchase':
            product.quantity += qty
        else:
            if product.quantity < qty:
                flash(f'Insufficient stock. Available: {product.quantity}, Requested: {qty}.', 'warning')
                return redirect(url_for('products'))
            product.quantity = max(0, product.quantity - qty)
        
        # Record transaction with current user
        tx = Transaction(
            product_id=product.id, 
            quantity=qty, 
            action=action, 
            user_id=current_user.id if current_user.is_authenticated else None,
            username=current_user.username if current_user.is_authenticated else 'System',
            notes=notes
        )
        db.session.add(tx)
        db.session.commit()
        flash(f'Stock {action} successful for {product.name}. New quantity: {product.quantity}', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating stock: {str(e)}', 'danger')
    
    return redirect(url_for('products'))


# ---------------------- DELETE PRODUCT ----------------------
@app.route('/delete_product/<int:id>', methods=['POST'])
@login_required
@admin_required
def delete_product(id):
    product = Product.query.get_or_404(id)
    try:
        db.session.delete(product)
        db.session.commit()
        flash(f'Product "{product.name}" deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting product: {str(e)}', 'danger')
    return redirect(url_for('products'))


# ---------------------- TRANSACTIONS LIST ----------------------
@app.route('/transactions')
@login_required
def transactions():
    query = Transaction.query.order_by(Transaction.timestamp.desc())
    
    # Filter by product if specified
    product_id = request.args.get('product_id', type=int)
    if product_id:
        query = query.filter(Transaction.product_id == product_id)
    
    pagination = get_pagination(request, query, per_page=30)
    return render_template('transactions.html', pagination=pagination, transactions=pagination.items)


# ---------------------- REPORTS ----------------------
@app.route('/reports')
@login_required
def reports():
    total_products = Product.query.count()
    low_stock_count = Product.query.filter(Product.quantity <= Product.min_threshold).count()
    total_transactions = Transaction.query.count()
    
    # Get statistics by category
    from sqlalchemy import func
    category_stats = db.session.query(
        Product.category,
        func.count(Product.id).label('count'),
        func.sum(Product.quantity).label('total_qty')
    ).group_by(Product.category).all()
    
    return render_template('reports.html', 
                         total_products=total_products,
                         low_stock_count=low_stock_count,
                         total_transactions=total_transactions,
                         category_stats=category_stats)


# ---------------------- AUTOMATED RECONCILE (Scheduler) ----------------------
def reconcile_all_stocks(commit_per_product=True):
    """Recompute product quantities from transaction history and update Product.quantity.

    This function calculates quantity as sum(purchase) - sum(use) for each product and
    updates the `Product.quantity` field if it differs. It also creates an 'adjust' Transaction
    record to audit the change made by the scheduler.

    commit_per_product: if True commit after updating each product to avoid large transactions.
    """
    logging.info("Starting reconcile_all_stocks job")
    with app.app_context():
        products = Product.query.all()
        for p in products:
            purchases = db.session.query(func.coalesce(func.sum(Transaction.quantity), 0)).filter(
                Transaction.product_id == p.id,
                Transaction.action == 'purchase'
            ).scalar() or 0

            uses = db.session.query(func.coalesce(func.sum(Transaction.quantity), 0)).filter(
                Transaction.product_id == p.id,
                Transaction.action == 'use'
            ).scalar() or 0

            try:
                computed = int(purchases) - int(uses)
            except Exception:
                computed = 0

            if computed < 0:
                computed = 0

            if p.quantity != computed:
                old = p.quantity
                diff = computed - old
                p.quantity = computed
                # record an adjustment transaction for audit
                adj_tx = Transaction(
                    product_id=p.id,
                    quantity=abs(diff),
                    action='adjust',
                    username='Scheduler',
                    notes=f'Auto-reconcile: set from {old} to {computed}'
                )
                try:
                    db.session.add(adj_tx)
                    if commit_per_product:
                        db.session.commit()
                    logging.info(f"Reconciled product id={p.id} name={p.name}: {old} -> {computed}")
                except Exception as e:
                    db.session.rollback()
                    logging.exception(f"Failed to save reconciliation for product id={p.id}: {e}")

        # if we deferred committing per product, commit now
        if not commit_per_product:
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
                logging.exception("Failed to commit batch reconciliation")


@app.route('/admin/run_reconcile', methods=['POST', 'GET'])
@login_required
@admin_required
def run_reconcile():
    """Manual endpoint for admins to run reconcile immediately (for testing)."""
    try:
        reconcile_all_stocks()
        flash('Reconcile job completed successfully.', 'success')
    except Exception as e:
        flash(f'Reconcile job failed: {str(e)}', 'danger')
    return redirect(url_for('dashboard'))


# ---------------------- EXPORT CSV (Products) ----------------------
@app.route('/export/products/csv')
@login_required
def export_products_csv():
    products = Product.query.join(Inventory).all()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Name', 'Category', 'Quantity', 'Min Threshold', 'Inventory', 'Location'])
    for p in products:
        inv_name = p.inventory.name if p.inventory else ''
        inv_loc = p.inventory.location if p.inventory else ''
        writer.writerow([p.id, p.name, p.category or '', p.quantity, p.min_threshold, inv_name, inv_loc])
    
    output.seek(0)
    return Response(output.getvalue(),
                    mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment;filename=products_report.csv'})


# ---------------------- EXPORT CSV (Transactions) ----------------------
@app.route('/export/transactions/csv')
@login_required
def export_transactions_csv():
    txs = Transaction.query.order_by(Transaction.timestamp.desc()).all()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Product', 'Action', 'Quantity', 'User', 'Timestamp', 'Notes'])
    for t in txs:
        pname = t.product.name if t.product else ''
        writer.writerow([
            t.id, pname, t.action, t.quantity, 
            t.get_user_name(), 
            t.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            t.notes or ''
        ])
    
    output.seek(0)
    return Response(output.getvalue(),
                    mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment;filename=transactions_report.csv'})


# ---------------------- EXPORT PDF (Products) ----------------------
@app.route('/export/products/pdf')
@login_required
def export_products_pdf():
    products = Product.query.join(Inventory).all()
    
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, height - 50, "SmartStock - Products Report")
    c.setFont("Helvetica", 9)
    c.drawString(50, height - 65, f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    c.drawString(50, height - 77, f"Total Products: {len(products)}")
    
    y = height - 100
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y, "ID")
    c.drawString(90, y, "Name")
    c.drawString(260, y, "Category")
    c.drawString(360, y, "Qty")
    c.drawString(410, y, "Min")
    c.drawString(450, y, "Inventory")
    
    y -= 14
    c.setFont("Helvetica", 9)
    
    for p in products:
        if y < 40:
            c.showPage()
            y = height - 50
            c.setFont("Helvetica", 9)
        inv_name = p.inventory.name if p.inventory else ''
        c.drawString(50, y, str(p.id))
        c.drawString(90, y, p.name[:25])
        c.drawString(260, y, (p.category or '')[:15])
        c.drawString(360, y, str(p.quantity))
        c.drawString(410, y, str(p.min_threshold))
        c.drawString(450, y, inv_name[:20])
        y -= 14
    
    c.save()
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name='products_report.pdf', mimetype='application/pdf')


# ---------------------- EXPORT PDF (Transactions) ----------------------
@app.route('/export/transactions/pdf')
@login_required
def export_transactions_pdf():
    txs = Transaction.query.order_by(Transaction.timestamp.desc()).all()
    
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, height - 50, "SmartStock - Transactions Report")
    c.setFont("Helvetica", 9)
    c.drawString(50, height - 65, f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    c.drawString(50, height - 77, f"Total Transactions: {len(txs)}")
    
    y = height - 100
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y, "ID")
    c.drawString(90, y, "Product")
    c.drawString(260, y, "Action")
    c.drawString(340, y, "Qty")
    c.drawString(380, y, "User")
    c.drawString(470, y, "Timestamp")
    
    y -= 14
    c.setFont("Helvetica", 9)
    
    for t in txs:
        if y < 40:
            c.showPage()
            y = height - 50
            c.setFont("Helvetica", 9)
        pname = t.product.name if t.product else ''
        c.drawString(50, y, str(t.id))
        c.drawString(90, y, pname[:20])
        c.drawString(260, y, t.action)
        c.drawString(340, y, str(t.quantity))
        c.drawString(380, y, (t.get_user_name())[:15])
        c.drawString(470, y, t.timestamp.strftime('%Y-%m-%d %H:%M'))
        y -= 14
    
    c.save()
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name='transactions_report.pdf', mimetype='application/pdf')


# ---------------------- RUN APP ----------------------
def _start_scheduler_if_needed():
    """Start a background scheduler for reconcile jobs.

    Only start when running the main process (avoids double-start with the reloader).
    Interval (seconds) can be configured via `app.config['RECONCILE_INTERVAL_SECONDS']`.
    """
    # Prevent multiple schedulers when using the Flask reloader
    if app.debug and os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        logging.info('Flask reloader main child not yet; skipping scheduler start in parent.')
        return

    interval = int(app.config.get('RECONCILE_INTERVAL_SECONDS', 300))  # default 5 minutes
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=reconcile_all_stocks, trigger='interval', seconds=interval, id='reconcile_all_stocks', replace_existing=True)
    scheduler.start()
    logging.info(f"Started reconcile scheduler: interval={interval}s")


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    with app.app_context():
        db.create_all()
        # Create default admin user if it doesn't exist
        if not AppUser.query.filter_by(username='admin').first():
            admin = AppUser(username='admin', email='admin@smartstock.com', role='admin')
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()
            print("Default admin user created: username='admin', password='admin123'")

    # Start scheduler (safe for Flask reloader)
    #_start_scheduler_if_needed()

    app.run(host='127.0.0.1', port=3000, debug=True)
