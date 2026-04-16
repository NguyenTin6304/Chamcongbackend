from html import escape

from app.services.mail.base import ExceptionNotificationMail, ResetPasswordMail


_EMAIL_FONT_STACK = (
    "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, "
    "'Helvetica Neue', sans-serif"
)

_EXCEPTION_EVENT_LABELS = {
    "exception_detected_employee": "Cần giải trình ngoại lệ chấm công",
    "exception_detected_admin": "Ngoại lệ chấm công cần admin xử lý",
    "exception_submitted_admin": "Nhân viên đã gửi giải trình ngoại lệ",
    "exception_approved_employee": "Giải trình ngoại lệ đã được phê duyệt",
    "exception_rejected_employee": "Giải trình ngoại lệ đã bị từ chối",
    "exception_expired_employee": "Ngoại lệ chấm công đã quá hạn giải trình",
    "exception_expire_reminder_employee": "Nhắc hạn giải trình ngoại lệ chấm công",
}

_METADATA_LABELS = {
    "employee_name": "Nhân viên",
    "exception_type": "Loại ngoại lệ",
    "status": "Trạng thái",
    "work_date": "Ngày công",
    "detected_at": "Thời điểm phát hiện",
    "expires_at": "Hạn giải trình",
    "admin_note": "Ghi chú admin",
}


def _wrap_html(body: str) -> str:
    return (
        '<!doctype html><html><head><meta charset="utf-8"></head>'
        f'<body style="margin:0;padding:16px;font-family:{_EMAIL_FONT_STACK};'
        'font-size:14px;line-height:1.5;color:#111827;">'
        f"{body}"
        "</body></html>"
    )


def build_reset_password_subject() -> str:
    return "Khôi phục mật khẩu - Chấm Công"


def build_reset_password_text(payload: ResetPasswordMail) -> str:
    return (
        "Xin chào,\n\n"
        "Bạn vừa yêu cầu đặt lại mật khẩu cho tài khoản Chấm Công.\n"
        f"Link đặt lại mật khẩu (hiệu lực {payload.expires_minutes} phút):\n"
        f"{payload.reset_url}\n\n"
        "Nếu link không mở được, hãy copy token bên dưới và dán thủ công vào màn Đặt lại mật khẩu:\n"
        f"{payload.reset_token}\n\n"
        "Nếu bạn không yêu cầu thao tác này, vui lòng bỏ qua email.\n"
    )


def build_reset_password_html(payload: ResetPasswordMail) -> str:
    reset_url = escape(payload.reset_url)
    reset_token = escape(payload.reset_token)
    return _wrap_html(
        "<p>Xin chào,</p>"
        "<p>Bạn vừa yêu cầu đặt lại mật khẩu cho tài khoản Chấm Công.</p>"
        f"<p>Link đặt lại mật khẩu (hiệu lực <b>{payload.expires_minutes} phút</b>):<br>"
        f'<a href="{reset_url}">{reset_url}</a></p>'
        "<p>Nếu link không mở được, hãy copy mã token bên dưới và dán thủ công vào màn Đặt lại mật khẩu:</p>"
        f"<p><code>{reset_token}</code></p>"
        "<p>Nếu bạn không yêu cầu thao tác này, vui lòng bỏ qua email.</p>"
    )


def build_exception_notification_subject(event_type: str) -> str:
    label = _EXCEPTION_EVENT_LABELS.get(
        event_type,
        "Cập nhật ngoại lệ chấm công",
    )
    return f"{label} - Chấm Công"


def build_exception_notification_text(payload: ExceptionNotificationMail) -> str:
    lines = [
        "Xin chào,",
        "",
        _EXCEPTION_EVENT_LABELS.get(
            payload.event_type,
            "Có cập nhật ngoại lệ chấm công.",
        ),
    ]
    for key in _METADATA_LABELS:
        value = payload.metadata.get(key)
        if value not in (None, ""):
            lines.append(f"{_METADATA_LABELS[key]}: {value}")
    lines.extend(
        [
            "",
            "Vui lòng đăng nhập hệ thống Chấm Công để xem chi tiết.",
        ],
    )
    return "\n".join(lines)


def build_exception_notification_html(payload: ExceptionNotificationMail) -> str:
    rows = []
    for key in _METADATA_LABELS:
        value = payload.metadata.get(key)
        if value not in (None, ""):
            rows.append(
                f"<li><b>{escape(_METADATA_LABELS[key])}</b>: {escape(str(value))}</li>",
            )
    details = "".join(rows)
    label = _EXCEPTION_EVENT_LABELS.get(
        payload.event_type,
        "Có cập nhật ngoại lệ chấm công.",
    )
    details_block = f"<ul>{details}</ul>" if details else ""
    return _wrap_html(
        "<p>Xin chào,</p>"
        f"<p>{escape(label)}</p>"
        f"{details_block}"
        "<p>Vui lòng đăng nhập hệ thống Chấm Công để xem chi tiết.</p>",
    )
