from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from flask import flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from . import bp
from .forms import ChangePasswordForm, LoginForm, LogoutForm
from ..extensions import db
from ..models import Role, User
from ..services.requests import add_audit


def is_safe_redirect(target: str) -> bool:
    host_url = urlparse(request.host_url)
    redirect_url = urlparse(urljoin(request.host_url, target))
    return redirect_url.scheme in {"http", "https"} and host_url.netloc == redirect_url.netloc


def home_for(user: User):
    endpoint = "admin.dashboard" if user.role == Role.ADMIN else "student.dashboard"
    return url_for(endpoint)


def masked_username(value: str) -> str:
    value = value.strip()
    if not value:
        return "(空白)"
    if len(value) <= 2:
        return value[0] + "*"
    return f"{value[0]}{'*' * min(len(value) - 2, 6)}{value[-1]}"


@bp.before_app_request
def enforce_temporary_password_change():
    if not current_user.is_authenticated or not current_user.must_change_password:
        return None
    allowed = {"auth.change_password", "auth.logout", "static"}
    if request.endpoint not in allowed:
        flash("此帳號使用臨時密碼，請先設定新密碼。", "warning")
        return redirect(url_for("auth.change_password"))
    return None


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(home_for(current_user))

    form = LoginForm()
    if form.validate_on_submit():
        user = db.session.scalar(
            db.select(User).where(User.username == form.username.data.strip())
        )
        if user is None or not user.is_active or not user.check_password(form.password.data):
            add_audit(
                user.id if user is not None else None,
                "LOGIN_FAILED",
                "User",
                user.id if user is not None else 0,
                f"登入失敗，帳號：{masked_username(form.username.data)}",
            )
            db.session.commit()
            flash("帳號或密碼錯誤。", "danger")
            return render_template("auth/login.html", form=form), 401

        user.last_login_at = datetime.now(timezone.utc)
        add_audit(user.id, "LOGIN_SUCCEEDED", "User", user.id, "帳號登入成功")
        db.session.commit()
        login_user(user)
        session.permanent = True
        flash("登入成功。", "success")

        next_url = request.args.get("next")
        if next_url and is_safe_redirect(next_url):
            return redirect(next_url)
        return redirect(home_for(user))

    return render_template("auth/login.html", form=form)


@bp.post("/logout")
@login_required
def logout():
    form = LogoutForm()
    if not form.validate_on_submit():
        return "CSRF validation failed", 400
    add_audit(current_user.id, "LOGOUT", "User", current_user.id, "帳號安全登出")
    db.session.commit()
    logout_user()
    session.clear()
    flash("您已安全登出。", "success")
    return redirect(url_for("auth.login"))


@bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            flash("目前密碼不正確。", "danger")
        elif current_user.check_password(form.new_password.data):
            flash("新密碼不可與目前密碼相同。", "danger")
        else:
            current_user.set_password(form.new_password.data)
            current_user.must_change_password = False
            add_audit(
                current_user.id,
                "PASSWORD_CHANGED",
                "User",
                current_user.id,
                "使用者修改自己的密碼",
            )
            db.session.commit()
            flash("密碼已更新。", "success")
            return redirect(home_for(current_user))
    return render_template("auth/change_password.html", form=form)
