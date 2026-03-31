import firebase_admin
from firebase_admin import credentials, messaging
import base64, random
import bcrypt
import smtplib
import socket
import ssl
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, parseaddr
from django.conf import settings
from rest_framework.response import Response
from rest_framework import status
from .models import ManageNotification, UserTransactionHistory
from django.core.files.storage import FileSystemStorage
from django.template.loader import render_to_string
import threading
from common.logs_file import logger
from .pagination import CustomPagination


_firebase_app = None


def get_firebase_app():
    global _firebase_app

    if _firebase_app is not None:
        return _firebase_app

    if firebase_admin._apps:
        _firebase_app = firebase_admin.get_app()
        return _firebase_app

    credentials_path = Path(settings.FIREBASE_CREDENTIAL_PATH).expanduser()
    if not credentials_path.is_absolute():
        credentials_path = Path(settings.BASE_DIR) / credentials_path

    if not credentials_path.exists():
        raise FileNotFoundError(
            f"Firebase credentials file not found at {credentials_path}."
        )

    cred = credentials.Certificate(str(credentials_path))
    _firebase_app = firebase_admin.initialize_app(cred)
    return _firebase_app


def send_push_notification(title, msg, registration_token, dataObject=None):
    get_firebase_app()

    message1 = messaging.MulticastMessage(
        notification=messaging.Notification(
            title=title,
            body=msg,
        ),
        data=dataObject or {},
        tokens=registration_token
    )
    response = messaging.send_multicast(message1)
    return response


def random_six_digits():
    # Generate a random 6-digit OTP
    six_digits = ''.join(random.choices('0123456789', k=6))
    return six_digits


def generate_token(token_input):
    # Generating a base64 token
    token = base64.b64encode(bytes(str(token_input), 'utf-8'))
    token = token.decode('ascii')
    return token


def save_notification(session_token, title, message, firebase_token, web_firebase_token, notification_for_booking=None):
    ManageNotification.objects.create(
        notification_title=title,
        notification_message=message,
        firebase_token=firebase_token,
        web_firebase_token=web_firebase_token,
        notification_for_user=session_token,
        notification_for_booking=notification_for_booking
    )
    return "Success"


def check_photo_format_and_size(file):
    max_photo_size = 2.0
    if not file.name.lower().endswith(('.png', '.jpg', '.jpeg')):
        return False
    if file.size > max_photo_size * 1024 * 1024:
        return False
    return True


def check_file_format_and_size(file):
    max_file_size = 10.0
    if not file.name.lower().endswith(('.png', '.jpg', '.jpeg', '.pdf', '.doc', '.docx')):
        return False
    if file.size > max_file_size * 1024 * 1024:
        return False
    return True


def save_file_in_directory(file):
    fss = FileSystemStorage()
    return fss.save(file.name, file)


def delete_file_from_directory(file_path):
    if file_path:
        fss = FileSystemStorage()
        if fss.exists(file_path):
            fss.delete(file_path)


def validate_required_fields(required_fields, data):
    missing_fields = [field.replace('_', ' ').capitalize() for field in required_fields if field not in data]
    if missing_fields:
        return Response({"message": f"Missing required field: {', '.join(missing_fields)}"}, status=status.HTTP_400_BAD_REQUEST)


class EmailThread(threading.Thread):
    def __init__(self, email, subject, html_content):
        self.email = email
        self.html_content = html_content
        self.subject = subject
        self.error = None
        self.sent = False
        threading.Thread.__init__(self, daemon=True)

    def run(self):
        self.sent = _send_email(self.email, self.subject, self.html_content)
        if not self.sent:
            self.error = RuntimeError(f"Unable to send email to {self.email}.")


def _resolve_email_sender():
    display_name, parsed_header_address = parseaddr(settings.EMAIL_ADDRESS)
    envelope_sender = (
        settings.EMAIL_ENVELOPE_SENDER
        or parsed_header_address
        or settings.SERVER_EMAIL
    )
    header_sender = (
        formataddr((display_name, parsed_header_address or envelope_sender))
        if display_name
        else (parsed_header_address or envelope_sender)
    )
    return header_sender, envelope_sender


def _resolve_email_recipient(email):
    if not email:
        return ""
    return parseaddr(str(email))[1].strip()


def _build_email_message(email, subject, html_content):
    header_sender, envelope_sender = _resolve_email_sender()
    recipient_address = _resolve_email_recipient(email)
    if not recipient_address:
        logger.error("Email sending aborted for subject '%s': missing recipient address.", subject)
        return None, None, None

    msg = MIMEMultipart()
    msg['From'] = header_sender
    msg['To'] = recipient_address
    msg['Subject'] = subject
    msg.attach(MIMEText(html_content, 'html'))
    return msg, envelope_sender, recipient_address


def _open_smtp_connection(host, port, use_ssl, use_tls):
    connection_kwargs = {
        "host": host,
        "port": port,
        "timeout": settings.EMAIL_SEND_TIMEOUT_SECONDS,
    }
    if settings.EMAIL_LOCAL_HOSTNAME:
        connection_kwargs["local_hostname"] = settings.EMAIL_LOCAL_HOSTNAME

    if use_ssl:
        return smtplib.SMTP_SSL(**connection_kwargs)

    mailserver = smtplib.SMTP(**connection_kwargs)
    mailserver.ehlo()
    if use_tls:
        mailserver.starttls(context=ssl.create_default_context())
        mailserver.ehlo()
    return mailserver


def _send_email_via_smtp(email, subject, html_content, *, host, port, use_ssl, use_tls):
    msg, envelope_sender, recipient_address = _build_email_message(email, subject, html_content)
    if not msg:
        return False

    with _open_smtp_connection(host, port, use_ssl, use_tls) as mailserver:
        mailserver.login(settings.SERVER_EMAIL, settings.SERVER_EMAIL_PASSWORD)
        mailserver.sendmail(envelope_sender, [recipient_address], msg.as_string())
    return True


def _should_retry_with_starttls(error):
    retriable_error_types = (
        TimeoutError,
        socket.timeout,
        socket.gaierror,
        ssl.SSLError,
        smtplib.SMTPConnectError,
        smtplib.SMTPServerDisconnected,
    )
    return isinstance(error, retriable_error_types)


def _send_email(email, subject, html_content):
    delivery_backend = getattr(settings, "EMAIL_DELIVERY_BACKEND", "smtp").strip().lower()
    if delivery_backend == "console":
        recipient_address = _resolve_email_recipient(email)
        if not recipient_address:
            logger.error("Console email aborted for subject '%s': missing recipient address.", subject)
            return False
        logger.info(
            "Console email to %s with subject '%s'. HTML length=%s",
            recipient_address,
            subject,
            len(html_content or ""),
        )
        return True

    if delivery_backend == "disabled":
        logger.warning("Email delivery disabled. Skipping subject '%s'.", subject)
        return True

    primary_transport = {
        "host": settings.EMAIL_HOST,
        "port": settings.EMAIL_PORT,
        "use_ssl": settings.EMAIL_USE_SSL,
        "use_tls": settings.EMAIL_USE_TLS,
    }

    try:
        return _send_email_via_smtp(email, subject, html_content, **primary_transport)
    except Exception as primary_error:
        should_retry = (
            settings.EMAIL_ALLOW_STARTTLS_FALLBACK
            and primary_transport["use_ssl"]
            and settings.EMAIL_STARTTLS_PORT != settings.EMAIL_PORT
            and _should_retry_with_starttls(primary_error)
        )
        if should_retry:
            logger.warning(
                "Primary SMTP transport failed for %s: %s. Retrying with STARTTLS on port %s.",
                email,
                str(primary_error),
                settings.EMAIL_STARTTLS_PORT,
            )
            try:
                return _send_email_via_smtp(
                    email,
                    subject,
                    html_content,
                    host=settings.EMAIL_HOST,
                    port=settings.EMAIL_STARTTLS_PORT,
                    use_ssl=False,
                    use_tls=True,
                )
            except Exception as fallback_error:
                logger.error(
                    "Email sending error for %s after STARTTLS fallback: %s",
                    email,
                    str(fallback_error),
                )
                return False

        if isinstance(primary_error, smtplib.SMTPException):
            logger.error("SMTP Error while sending email to %s: %s", email, str(primary_error))
        else:
            logger.error("Email sending error for %s: %s", email, str(primary_error))
        return False


def _dispatch_email(email, subject, html_content, wait_for_result=False):
    if wait_for_result:
        return _send_email(email, subject, html_content)

    email_thread = EmailThread(email, subject, html_content)
    email_thread.start()
    return True


def send_verification_email(email, name, verification_otp, wait_for_result=False):
    subject = 'Verify your email address'
    html_content = render_to_string('emails/verify-email.html', {
        'verification_otp': verification_otp,
        'email': email,
        'otp_expiry_minutes': settings.EMAIL_OTP_EXPIRY_MINUTES,
    })
    is_sent = _dispatch_email(email, subject, html_content, wait_for_result=wait_for_result)
    if wait_for_result:
        return is_sent
    return "Verification email is being sent"


def send_company_approval_email(email, name):
    subject = 'Your company account is activated'
    html_content = render_to_string('emails/company-approved.html', {
        'email': email
    })
    _dispatch_email(email, subject, html_content)
    return "Company approval email is being sent"


def send_objection_email(email, name, booking_number, remarks):
    subject = f'Objection raised against your Booking number: {booking_number}'
    html_content = render_to_string('emails/objection-raised.html', {
        'email': email,
        'name': name,
        'booking_number': booking_number,
        'remarks': remarks
    })
    _dispatch_email(email, subject, html_content)
    return "Objection email is being sent"


def send_complaint_email(email, name, booking_number, remarks):
    subject = f'Complaint raised against your Booking number: {booking_number}'
    html_content = render_to_string('emails/complaint-raised.html', {
        'email': email,
        'name': name,
        'booking_number': booking_number,
        'remarks': remarks
    })
    _dispatch_email(email, subject, html_content)
    return "Complaint email is being sent"


def send_payment_verification_email(email, name, booking_number):
    subject = f'Booking payment is verified'
    html_content = render_to_string('emails/payment-verified.html', {
        'email': email,
        'name': name,
        'booking_number': booking_number
    })
    _dispatch_email(email, subject, html_content)
    return "Payment verification email is being sent"


def send_payment_rejection_email(email, name, booking_number, review_message):
    subject = f'Payment update needed for booking {booking_number}'
    html_content = render_to_string('emails/payment-rejected.html', {
        'email': email,
        'name': name,
        'booking_number': booking_number,
        'review_message': review_message,
    })
    _dispatch_email(email, subject, html_content)
    return "Payment rejection email is being sent"


def send_new_order_email(email, name, package_type, package_name, start_date,  adults, infants, child, total_price, booking_number):
    subject = f'New Booking Received - {booking_number}'
    html_content = render_to_string('emails/partner-new-booking.html', {
        'email': email,
        'name': name,
        'booking_number': booking_number,
        'start_date': start_date,
        'package_type': package_type,
        'package_name': package_name,
        'adults': adults,
        'total_price': total_price
    })
    _dispatch_email(email, subject, html_content)
    return "New order booking email is being sent"


def send_booking_documents_email(email, name, booking_number, document_type):
    subject = f'{document_type} confirmation against Booking number: {booking_number}'
    html_content = render_to_string('emails/documents.html', {
        'email': email,
        'name': name,
        'booking_number': booking_number,
        'document_type': document_type
    })
    _dispatch_email(email, subject, html_content)
    return "Document email is being sent"


def new_user_welcome_email(email, name):
    subject = f'Welcome to the HajjUmrah.co Family!'
    html_content = render_to_string('emails/new-user-welcome.html', {
        'email': email,
        'name': name
    })
    _dispatch_email(email, subject, html_content)
    return "new user welcome email is being sent"


def user_subscribe_email(email):
    subject = f'Subscription Confirmation – Stay Tuned!'
    html_content = render_to_string('emails/subscribe.html', {
        'email': email
    })
    _dispatch_email(email, subject, html_content)
    return "subscribe email is being sent"


def forgot_password_email(email, forgot_link):
    subject = f'Forgot Password Request'
    html_content = render_to_string('emails/forgot-password.html', {
        'email': email,
        'forgot_link': forgot_link
    })
    _dispatch_email(email, subject, html_content)
    return "forgot email is being sent"


def user_new_booking_email(email, name, package_type, package_name, booking_number, adults, child, infants, start_date, total_amount, paid_amount):
    subject = f'{package_type} Booking Confirmation'
    html_content = render_to_string('emails/user-new-booking.html', {
        'email': email,
        'name': name,
        'package_type': package_type,
        'package_name': package_name,
        'booking_number': booking_number,
        'adults': adults,
        'child': child,
        'infants': infants,
        'start_date': start_date,
        'paid_amount': paid_amount,
        'remaining_amount': int(total_amount)-int(paid_amount),
        'total_amount': total_amount
    })
    _dispatch_email(email, subject, html_content)
    return "new user welcome email is being sent"


def preparation_email(email, name, package_type):
    subject = f'Important Checklist for Your {package_type} Journey'
    html_content = render_to_string('emails/prepration_tips.html', {
        'email': email,
        'name': name,
        'package_type': package_type,
    })
    _dispatch_email(email, subject, html_content)
    return "checklist email is being sent"

# def CreatePartnerTransaction(transaction_code, transaction_amount, transaction_type, transaction_partner_token, transaction_wallet_token, transaction_description, transaction_for_package):
#     ln = UserTransactionHistory.objects.create(
#         transaction_code=transaction_code,
#         transaction_amount=transaction_amount,
#         transaction_type=transaction_type,
#         transaction_user_token=transaction_partner_token,
#         transaction_wallet_token=transaction_wallet_token,
#         transaction_description=transaction_description,
#         transaction_for_package=transaction_for_package
#     )
#     return "Success"


def hash_password(password):
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed.decode('utf-8')


def check_password(hashed_password, user_password):
    if isinstance(hashed_password, str):
        hashed_password = hashed_password.encode('utf-8')
    if isinstance(user_password, str):
        user_password = user_password.encode('utf-8')
    return bcrypt.checkpw(user_password, hashed_password)


# def send_docuements_emails(email, name, title, type, file_url):
#     try:
#         msg = MIMEMultipart()
#         msg['From'] = settings.EMAIL_ADDRESS
#         msg['To'] = email
#         msg['Subject'] = title
#         html = f"{mailbody2.part_one}{name}{mailbody2.part_two}{type}{mailbody2.part_three}{file_url}{mailbody2.part_four}"
#         msg.attach(MIMEText(html, 'html'))
#         with smtplib.SMTP_SSL(settings.EMAIL_HOST, 465) as mailserver:
#             mailserver.login(settings.SERVER_EMAIL, settings.SERVER_EMAIL_PASSWORD)
#             mailserver.sendmail(settings.EMAIL_ADDRESS, email, msg.as_string())
#     except Exception as e:
#         print(f"Failed to send verification email: {str(e)}")
