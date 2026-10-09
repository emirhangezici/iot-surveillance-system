from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, session
import pyodbc
from datetime import datetime

import config

app = Flask(__name__)

if not config.SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY is not set. Copy .env.example to .env and set a secret key."
    )
app.config['SECRET_KEY'] = config.SECRET_KEY

def get_db_connection():
    conn = pyodbc.connect(config.database_connection_string())
    return conn

def validate_user(username, password, userType):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM Users WHERE UserName=? AND Password=? AND UserType=?", (username, password, userType))
    user = cursor.fetchone()
    conn.close()
    return user

def getUsers():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM Users")
    users = cursor.fetchall()
    conn.close()
    return users

def userIsAdmin(user):
    if hasattr(user, 'UserType') and user.UserType == "Admin":
        return True
    else:
        return False

def auth_required(route_name):
    def decorator(f):
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('login'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator
def auth_required_and_admin(route_name):
    def decorator(f):
        def decorated_function(*args, **kwargs):
            if 'isAdmin' in session and session['isAdmin'] == True:
                return f(*args, **kwargs)
            else:
                flash("Error: You cannot access this page.")
                return redirect(url_for('admin_login'))
        return decorated_function
    return decorator

# Define log functions
def log_event(event_type, userId):
    conn = get_db_connection()
    cursor = conn.cursor()
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute("INSERT INTO EventLogs ([EventType], [TimeStamp], [UserId]) VALUES (?, ?, ?)", (event_type, timestamp, userId))
    conn.commit()
    conn.close()

def get_recent_logs(userId=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    if userId is None:
        cursor.execute(f"""
            SELECT el.ID, el.EventType, el.TimeStamp, u.Username
            FROM EventLogs el
            LEFT JOIN Users u ON el.UserId = u.ID
            ORDER BY el.TimeStamp DESC
        """)
    else:
        cursor.execute(f"SELECT ID, EventType, TimeStamp FROM EventLogs WHERE UserId=? ORDER BY TimeStamp DESC", (userId))
    logs = cursor.fetchall()
    conn.close()
    return logs

def delete_log(log_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE from EventLogs WHERE ID=?", (log_id,))
    rows_affected = cursor.rowcount
    conn.commit()
    conn.close()
    return rows_affected > 0

# Route for the login page
@app.route('/')
def login():
    return render_template('login.html')

# Handle login form submission
@app.route('/login', methods=['POST'])
def login_post():
    username = request.form['username']
    password = request.form['password']
    reqUserType = request.form['user_type']
    user = validate_user(username, password, reqUserType)
    if user:
        session['user_id'] = user.ID
        session['isAdmin'] = user.UserType == "Admin"
        if userIsAdmin(user):
            return redirect(url_for('admin'))
        else:
            return redirect(url_for('dashboard'))
    else:
        flash("Error: Invalid credentials.")
        return render_template('login.html')

@app.route('/dashboard',  endpoint='dashboard')
@auth_required('dashboard')
def dashboard():
    logs = get_recent_logs(session['user_id'])

    return render_template('dashboard.html', logs=logs)

# Route to control the alarm
@app.route('/control_alarm', methods=['POST'], endpoint='control_alarm')
@auth_required('control_alarm')
def control_alarm():
    action = request.form.get('action')
    if action == 'enable':
        log_event("Alarm Enabled", session['user_id'])
        status = "Alarm Enabled"
    elif action == 'disable':
        log_event("Alarm Disabled", session['user_id'])
        status = "Alarm Disabled"
    else:
        return "Invalid action", 400
    
    logs = get_recent_logs(session['user_id'])
    logs_json = [{"ID": log.ID, "EventType": log.EventType, "TimeStamp": log.TimeStamp} for log in logs]
    return jsonify({"logs": logs_json, "status": status})

@app.route('/delete_log', methods=['POST'], endpoint='delete_log')
@auth_required('delete_log')
def delete_log_post():
    log_id = request.form['log_id']
    deleted = delete_log(log_id)
    if deleted:
        flash("Success: Log deleted successfully")
    else:
        flash("Error: Log not found")
    return redirect(request.referrer)

@app.route('/admin_login', methods=['GET'])
def admin_login():
    return render_template('admin_login.html')

@app.route('/admin', methods=['GET'], endpoint='admin')
@auth_required_and_admin('admin')
def admin():
    logs = get_recent_logs()
    users = getUsers()
    return render_template('admin.html', logs=logs, users=users)
@app.route('/delete_user', methods=['POST'], endpoint='delete_user')
@auth_required_and_admin('delete_user')
def delete_user():
    user_id = request.form['user_id']
    if user_id == str(session['user_id']):
        flash("Error: You cannot delete your own account.")
        return redirect(request.referrer)
    else:
        conn = get_db_connection()
        cursor = conn.cursor()

        # get user
        cursor.execute("SELECT * FROM Users WHERE ID=?", (user_id,))
        user = cursor.fetchone()

        if user:
            cursor.execute("DELETE FROM EventLogs WHERE UserId=?", (user_id,))
            cursor.execute("DELETE FROM Users WHERE ID=?", (user_id,))
            conn.commit()
            flash("Success: User and user logs deleted successfully")
        else:
            flash("Error: User not found")

        conn.close()
        return redirect(request.referrer)

@app.route('/add_user', methods=['POST'], endpoint='add_user')
@auth_required_and_admin('add_user')
def add_user():
    username = request.form['username']
    password = request.form['password']
    user_type = request.form['user_type']
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO Users (UserName, Password, UserType) VALUES (?, ?, ?)", (username, password, user_type))
    conn.commit()
    conn.close()
    flash("Success: User added successfully")
    return redirect(request.referrer)

@app.route('/logout', methods=['GET'], endpoint='logout')
@auth_required('logout')
def logout():
    session.pop('user_id', None)
    session.pop('isAdmin', None)
    return redirect(url_for('login'))

@app.route("/setPin", methods=['POST'], endpoint='setPin')
@auth_required('setPin')
def setPin():
    pin = request.form['pin']
    if len(pin) != 4:
        return jsonify({"status": False, "message": "Pin must be 4 digits"})

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE Users SET PinCode=? WHERE ID=?", (pin,session["user_id"]))
        conn.commit()
        rows_affected = cursor.rowcount
        if rows_affected > 0:
            return jsonify({"status": True })
        else:
            return jsonify({"status": False, "message": "Please try again later+"})
    except Exception as e:
        return jsonify({"status": False, "message": str(e)})
    finally:
        conn.close()


# Run the application
if __name__ == "__main__":
    app.run(debug=True)
