from app.services.mail.base import ResetPasswordMail


def build_reset_password_subject() -> str:
    return "Khôi phục mật khẩu - Chấm Công"


def build_reset_password_text(payload: ResetPasswordMail) -> str:
    return (
        "Xin chào,\n\n"
        "Bạn vừa yêu cầu đặt lại mật khẩu cho tài khoản Chấm Công.\n"
        f"Link đặt lại mật khẩu (hiệu lực {payload.expires_minutes} phút):\n"
        f"{payload.reset_url}\n\n"
        "Nếu link không mở được, hãy copy token bên dưới và dán thủ công vào màn Đặt lại mật khẩu:\n"
        f"{payload.reset_token}\n\n"
        "Nếu bạn không yêu cầu thao tác này, vui lòng bỏ qua email.\n"
    )


def build_reset_password_html(payload: ResetPasswordMail) -> str:
    return (
        "<p>Xin chào,</p>"
        "<p>Bạn vừa yêu cầu đặt lại mật khẩu cho tài khoản Chấm Công.</p>"
        f"<p>Link đặt lại mật khẩu (hiệu lực <b>{payload.expires_minutes} phút</b>):<br>"
        f"<a href=\"{payload.reset_url}\">{payload.reset_url}</a></p>"
        "<p>Nếu link không mở được, hãy copy token bên dưới và dán thủ công vào màn Đặt lại mật khẩu:</p>"
        f"<p><code>{payload.reset_token}</code></p>"
        "<p>Nếu bạn không yêu cầu thao tác này, vui lòng bỏ qua email.</p>"
    )
