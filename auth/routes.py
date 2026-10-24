# /auth/routes.py

import datetime
import secrets
import threading
import time
from functools import wraps

import bcrypt
import jwt
from flask import (
    Blueprint,
    current_app,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from .config import (
    BASE_URL,
    COOKIE_SECURE,
    JWT_EXPIRATION,
    JWT_SECRET,
    RESET_TOKEN_EXPIRATION,
    VERIFICATION_EXPIRATION,
)
from .db import (
    create_refresh_token,
    create_user,
    find_refresh_token_by_raw,
    find_user_by_email,
    find_user_by_reset_token,
    get_all_users,
    revoke_all_user_tokens,
    revoke_refresh_token,
    update_user,
)
from .mail_service import send_email


# No URL prefix: existing URLs such as /login stay the same.
auth_bp = Blueprint(
    "auth",
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/auth/static",
)

# Connected to the Flask application in auth/__init__.py later.
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
)


@auth_bp.app_errorhandler(429)
def ratelimit_handler(error):
    return jsonify({
        "error": "Too many requests. Please slow down."
    }), 429


# ================================================================
# IP + USER ATTEMPT TRACKING
# ================================================================

ip_user_attempts = {}
IP_USER_LIMIT = 5
IP_USER_WINDOW = 300
IP_USER_LOCK = threading.Lock()


def get_client_ip():
    return request.remote_addr


def cleanup_old_attempts():
    now = time.time()

    with IP_USER_LOCK:
        for key in list(ip_user_attempts):
            ip_user_attempts[key] = [
                timestamp
                for timestamp in ip_user_attempts[key]
                if now - timestamp < IP_USER_WINDOW
            ]

            if not ip_user_attempts[key]:
                del ip_user_attempts[key]


def is_ip_user_rate_limited(ip, email):
    key = f"{ip}:{email}"
    now = time.time()

    with IP_USER_LOCK:
        if key not in ip_user_attempts:
            return False

        ip_user_attempts[key] = [
            timestamp
            for timestamp in ip_user_attempts[key]
            if now - timestamp < IP_USER_WINDOW
        ]

        if not ip_user_attempts[key]:
            del ip_user_attempts[key]
            return False

        return len(ip_user_attempts[key]) >= IP_USER_LIMIT


def add_ip_user_attempt(ip, email):
    key = f"{ip}:{email}"
    now = time.time()

    with IP_USER_LOCK:
        ip_user_attempts.setdefault(key, []).append(now)

        ip_user_attempts[key] = [
            timestamp
            for timestamp in ip_user_attempts[key]
            if now - timestamp < IP_USER_WINDOW
        ]


def clear_ip_user_attempts(ip, email):
    key = f"{ip}:{email}"

    with IP_USER_LOCK:
        ip_user_attempts.pop(key, None)


# ================================================================
# SHARED HELPERS
# ================================================================

def get_json_data():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def password_error(password):
    if not isinstance(password, str) or not password:
        return "Password required"

    if len(password) < 8:
        return "Password must be at least 8 characters"

    if not any(character.isupper() for character in password):
        return "Password must contain an uppercase letter"

    if not any(character.isdigit() for character in password):
        return "Password must contain a number"

    if not any(
        character in "!@#$%^&*()_-+=<>?/"
        for character in password
    ):
        return "Password must contain a special character (!@#$%^&*)"

    return None


def generate_jwt(email):
    payload = {
        "sub": email,
        "exp": datetime.datetime.utcnow() + JWT_EXPIRATION,
    }

    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def set_auth_cookies(response, access_token, refresh_token):
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="Lax",
        max_age=int(JWT_EXPIRATION.total_seconds()),
        path="/",
    )

    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="Lax",
        max_age=2592000,
        path="/",
    )

    return response


def clear_auth_cookies(response):
    for cookie_name in ("access_token", "refresh_token"):
        response.set_cookie(
            key=cookie_name,
            value="",
            expires=0,
            httponly=True,
            secure=COOKIE_SECURE,
            samesite="Lax",
            path="/",
        )

    return response


def is_authenticated():
    token = request.cookies.get("access_token")

    if not token:
        return False

    try:
        jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return True
    except jwt.InvalidTokenError:
        return False


def token_required(function):
    @wraps(function)
    def decorated(*args, **kwargs):
        token = request.cookies.get("access_token")

        if not token:
            return jsonify({"error": "Missing access token"}), 401

        try:
            payload = jwt.decode(
                token,
                JWT_SECRET,
                algorithms=["HS256"],
            )
            request.user_email = payload["sub"]
        except (jwt.InvalidTokenError, KeyError):
            return jsonify({
                "error": "Invalid or expired access token"
            }), 401

        return function(*args, **kwargs)

    return decorated


# ================================================================
# HEALTH CHECK
# ================================================================

@auth_bp.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "healthy",
        "database": "connected",
        "timestamp": datetime.datetime.utcnow().isoformat(),
    }), 200


# ================================================================
# HTML PAGES
# ================================================================

@auth_bp.route("/")
def home():
    return redirect("/login")


@auth_bp.route("/login", methods=["GET"])
def login_page():
    return render_template("auth/login.html")


@auth_bp.route("/register", methods=["GET"])
def register_page():
    return render_template("auth/register.html")


@auth_bp.route("/forgot-password", methods=["GET"])
def forgot_page():
    return render_template("auth/forgot_password.html")


@auth_bp.route("/reset-password", methods=["GET"])
def reset_page():
    return render_template(
        "auth/reset_password.html",
        token=request.args.get("token"),
    )


@auth_bp.route("/dashboard")
def dashboard_page():
    if not is_authenticated():
        return redirect("/login")

    return render_template("auth/dashboard.html")


@auth_bp.route("/profile")
def profile_page():
    if not is_authenticated():
        return redirect("/login")

    return render_template("auth/profile.html")


# ================================================================
# REGISTER
# ================================================================

@auth_bp.route("/register", methods=["POST"])
@limiter.limit("5 per minute")
def register():
    data = get_json_data()
    email = data.get("email")
    password = data.get("password")

    if (
        not isinstance(email, str)
        or not email
        or not isinstance(password, str)
        or not password
    ):
        return jsonify({
            "error": "Email and password required"
        }), 400

    error = password_error(password)

    if error:
        return jsonify({"error": error}), 400

    message = "If this email is valid, a verification link was sent."

    if find_user_by_email(email):
        return jsonify({"message": message}), 200

    hashed_password = bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt(),
    ).decode("utf-8")

    create_user(email, hashed_password)

    raw_token = secrets.token_urlsafe(32)
    hashed_token = bcrypt.hashpw(
        raw_token.encode("utf-8"),
        bcrypt.gensalt(),
    ).decode("utf-8")

    expiry = datetime.datetime.utcnow() + VERIFICATION_EXPIRATION

    update_user(email, {
        "verification_token": hashed_token,
        "verification_expiry": expiry,
    })

    verify_link = f"{BASE_URL}/verify-email?token={raw_token}"

    subject = "Verify your email"
    text = f"Welcome! Click the link to verify: {verify_link}"
    html = f"""
    <p>Welcome!</p>
    <p>Click the button below to verify:</p>
    <p>
        <a href="{verify_link}"
           style="display:inline-block;padding:12px 24px;
                  background:#007bff;color:#fff;text-decoration:none;
                  border-radius:6px;">
            Verify Email
        </a>
    </p>
    <p>Expires in 24h.</p>
    """

    try:
        send_email(email, subject, text, html)
    except Exception:
        current_app.logger.exception("Verification email failed")

    return jsonify({"message": message}), 200


# ================================================================
# VERIFY EMAIL
# ================================================================

@auth_bp.route("/verify-email", methods=["GET"])
def verify_email():
    raw_token = request.args.get("token")

    if not raw_token:
        return render_template(
            "auth/verify_error.html",
            error="Missing token.",
        ), 400

    user_found = None

    for user in get_all_users():
        if (
            user.verified
            or not user.verification_token
            or not user.verification_expiry
        ):
            continue

        if user.verification_expiry < datetime.datetime.utcnow():
            continue

        if bcrypt.checkpw(
            raw_token.encode("utf-8"),
            user.verification_token.encode("utf-8"),
        ):
            user_found = user
            break

    if not user_found:
        return render_template(
            "auth/verify_error.html",
            error="Invalid or expired token.",
        ), 400

    update_user(user_found.email, {
        "verified": True,
        "verification_token": None,
        "verification_expiry": None,
    })

    return render_template("auth/verify_success.html")


# ================================================================
# LOGIN
# ================================================================

@auth_bp.route("/login", methods=["POST"])
@limiter.limit("5 per minute")
def login():
    data = get_json_data()
    email = data.get("email")
    password = data.get("password")

    if (
        not isinstance(email, str)
        or not email
        or not isinstance(password, str)
        or not password
    ):
        return jsonify({
            "error": "Email and password required"
        }), 400

    ip = get_client_ip()
    cleanup_old_attempts()

    if is_ip_user_rate_limited(ip, email):
        return jsonify({
            "error": (
                "Too many failed login attempts from this IP "
                "for this user. Please wait 5 minutes."
            )
        }), 429

    user = find_user_by_email(email)

    if not user:
        time.sleep(0.3)
        add_ip_user_attempt(ip, email)
        return jsonify({"error": "Invalid credentials"}), 401

    now = datetime.datetime.utcnow()

    if user.locked_until and user.locked_until > now:
        remaining = int(
            (user.locked_until - now).total_seconds() / 60
        )

        return jsonify({
            "error": (
                f"Account locked. Try again in {remaining} minute(s)."
            )
        }), 403

    if not bcrypt.checkpw(
        password.encode("utf-8"),
        user.password.encode("utf-8"),
    ):
        time.sleep(0.3)
        add_ip_user_attempt(ip, email)

        attempts = (user.failed_login_attempts or 0) + 1
        updates = {"failed_login_attempts": attempts}

        if attempts >= 5:
            updates["locked_until"] = (
                datetime.datetime.utcnow()
                + datetime.timedelta(minutes=15)
            )

        update_user(email, updates)

        return jsonify({"error": "Invalid credentials"}), 401

    if not user.verified:
        return jsonify({
            "error": "Please verify your email first."
        }), 403

    update_user(email, {
        "failed_login_attempts": 0,
        "locked_until": None,
    })

    clear_ip_user_attempts(ip, email)

    access_token = generate_jwt(email)
    raw_refresh_token = secrets.token_urlsafe(32)
    refresh_expiry = (
        datetime.datetime.utcnow()
        + datetime.timedelta(seconds=2592000)
    )

    create_refresh_token(
        user.id,
        raw_refresh_token,
        refresh_expiry,
    )

    response = make_response(jsonify({
        "message": "Login successful"
    }))

    return set_auth_cookies(
        response,
        access_token,
        raw_refresh_token,
    )


# ================================================================
# REFRESH
# ================================================================

@auth_bp.route("/refresh", methods=["POST"])
def refresh():
    raw_refresh_token = request.cookies.get("refresh_token")

    if not raw_refresh_token:
        return jsonify({"error": "Missing refresh token"}), 401

    token_record = find_refresh_token_by_raw(raw_refresh_token)

    if not token_record:
        return jsonify({
            "error": "Invalid or expired refresh token"
        }), 401

    user = token_record.user

    revoke_refresh_token(token_record.id)

    new_access_token = generate_jwt(user.email)
    new_raw_refresh = secrets.token_urlsafe(32)
    refresh_expiry = (
        datetime.datetime.utcnow()
        + datetime.timedelta(seconds=2592000)
    )

    create_refresh_token(
        user.id,
        new_raw_refresh,
        refresh_expiry,
    )

    response = make_response(jsonify({
        "message": "Tokens refreshed successfully"
    }))

    return set_auth_cookies(
        response,
        new_access_token,
        new_raw_refresh,
    )


# ================================================================
# FORGOT PASSWORD
# ================================================================

@auth_bp.route("/forgot-password", methods=["POST"])
@limiter.limit("5 per minute")
def forgot_password():
    data = get_json_data()
    email = data.get("email")

    if not isinstance(email, str) or not email:
        return jsonify({"error": "Email required"}), 400

    token = secrets.token_urlsafe(32)
    hashed_token = bcrypt.hashpw(
        token.encode("utf-8"),
        bcrypt.gensalt(),
    ).decode("utf-8")

    user = find_user_by_email(email)

    if user:
        expiry = (
            datetime.datetime.utcnow()
            + RESET_TOKEN_EXPIRATION
        )

        update_user(email, {
            "reset_token": hashed_token,
            "reset_expiry": expiry,
        })

        reset_link = f"{BASE_URL}/reset-password?token={token}"

        subject = "Password Reset"
        text = f"Click the link to reset your password: {reset_link}"
        html = f"""
        <p>Click the button to reset your password:</p>
        <p>
            <a href="{reset_link}"
               style="display:inline-block;padding:12px 24px;
                      background:#28a745;color:#fff;text-decoration:none;
                      border-radius:6px;">
                Reset Password
            </a>
        </p>
        <p>Expires in 15 min.</p>
        """

        try:
            send_email(email, subject, text, html)
        except Exception:
            current_app.logger.exception("Password reset email failed")
    else:
        time.sleep(0.2)

    return jsonify({
        "message": "If that email exists, a reset link was sent"
    }), 200


# ================================================================
# RESET PASSWORD
# ================================================================

@auth_bp.route("/reset-password", methods=["POST"])
@limiter.limit("5 per minute")
def reset_password():
    data = get_json_data()
    token = data.get("token")
    new_password = data.get("new_password")

    if (
        not isinstance(token, str)
        or not token
        or not isinstance(new_password, str)
        or not new_password
    ):
        return jsonify({
            "error": "Token and password required"
        }), 400

    error = password_error(new_password)

    if error:
        return jsonify({"error": error}), 400

    user = find_user_by_reset_token(token)

    if not user:
        return jsonify({
            "error": "Invalid or expired token"
        }), 400

    if (
        user.reset_expiry
        and user.reset_expiry < datetime.datetime.utcnow()
    ):
        return jsonify({"error": "Token has expired"}), 400

    new_hashed = bcrypt.hashpw(
        new_password.encode("utf-8"),
        bcrypt.gensalt(),
    ).decode("utf-8")

    update_user(user.email, {
        "password": new_hashed,
        "reset_token": None,
        "reset_expiry": None,
    })

    revoke_all_user_tokens(user.id)

    return jsonify({
        "message": (
            "Password updated. You have been logged out of all devices."
        )
    }), 200


# ================================================================
# PROTECTED API EXAMPLE
# ================================================================

@auth_bp.route("/protected", methods=["GET"])
@token_required
def protected():
    return jsonify({
        "message": f"Hello {request.user_email}!"
    }), 200


# ================================================================
# LOGOUT
# ================================================================

@auth_bp.route("/logout", methods=["POST"])
def logout():
    raw_refresh_token = request.cookies.get("refresh_token")

    if raw_refresh_token:
        token_record = find_refresh_token_by_raw(raw_refresh_token)

        if token_record:
            revoke_refresh_token(token_record.id)

    response = make_response(jsonify({
        "message": "Logged out successfully"
    }))

    return clear_auth_cookies(response)


# ================================================================
# LOGOUT ALL DEVICES
# ================================================================

@auth_bp.route("/logout-all", methods=["POST"])
@token_required
def logout_all():
    user = find_user_by_email(request.user_email)

    if user:
        revoke_all_user_tokens(user.id)

    response = make_response(jsonify({
        "message": "Logged out of all devices successfully"
    }))

    return clear_auth_cookies(response)