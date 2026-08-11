from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, EqualTo, Length


class LoginForm(FlaskForm):
    username = StringField("帳號", validators=[DataRequired(message="請輸入帳號。")])
    password = PasswordField("密碼", validators=[DataRequired(message="請輸入密碼。")])
    submit = SubmitField("登入")


class LogoutForm(FlaskForm):
    submit = SubmitField("登出")


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField(
        "目前密碼", validators=[DataRequired(message="請輸入目前密碼。")]
    )
    new_password = PasswordField(
        "新密碼",
        validators=[
            DataRequired(message="請輸入新密碼。"),
            Length(min=8, max=128, message="密碼長度需為 8 至 128 個字元。"),
        ],
    )
    confirm_password = PasswordField(
        "確認新密碼",
        validators=[
            DataRequired(message="請再次輸入新密碼。"),
            EqualTo("new_password", message="兩次輸入的新密碼不一致。"),
        ],
    )
    submit = SubmitField("更新密碼")
