import firebase_admin
from firebase_admin import credentials, messaging
import base64, random
import bcrypt
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from django.conf import settings
from rest_framework.response import Response
from rest_framework import status
from .models import ManageNotification, UserTransactionHistory
from django.core.files.storage import FileSystemStorage
from rest_framework.pagination import PageNumberPagination
from django.template.loader import render_to_string
import threading


cred = credentials.Certificate("common/firebase.json")
firebase_admin.initialize_app(cred)


def send_push_notification(title, msg, registration_token, dataObject=None):

    message1 = messaging.MulticastMessage(
        notification=messaging.Notification(
            title=title,
            body=msg,
        ),
        data={},
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
        threading.Thread.__init__(self)

    def run(self):
        try:
            msg = MIMEMultipart()
            msg['From'] = settings.EMAIL_ADDRESS
            msg['To'] = self.email
            subject = self.subject
            html_content = self.html_content
            msg['Subject'] = subject
            msg.attach(MIMEText(html_content, 'html'))
            with smtplib.SMTP_SSL(settings.EMAIL_HOST, 465) as mailserver:
                mailserver.login(settings.SERVER_EMAIL, settings.SERVER_EMAIL_PASSWORD)
                mailserver.sendmail(settings.EMAIL_ADDRESS, self.email, msg.as_string())
        except smtplib.SMTPException as smtp_err:
            print(f"SMTP Error: {smtp_err}")
        except Exception as e:
            print(f"Error: {e}")


def send_verification_email(email, name, verification_otp):
    subject = 'Verify your email address'
    html_content = render_to_string('emails/verify-email.html', {
        'verification_otp': verification_otp,
        'email': email
    })
    email_thread = EmailThread(email, subject, html_content)
    email_thread.start()
    return "Verification email is being sent"


def send_company_approval_email(email, name):
    subject = 'Your company account is activated'
    html_content = render_to_string('emails/company-approved.html', {
        'email': email
    })
    email_thread = EmailThread(email, subject, html_content)
    email_thread.start()
    return "Company approval email is being sent"


def send_objection_email(email, name, booking_number, remarks):
    subject = f'Objection raised against your Booking number: {booking_number}'
    html_content = render_to_string('emails/objection-raised.html', {
        'email': email,
        'name': name,
        'booking_number': booking_number,
        'remarks': remarks
    })
    email_thread = EmailThread(email, subject, html_content)
    email_thread.start()
    return "Objection email is being sent"


def send_complaint_email(email, name, booking_number, remarks):
    subject = f'Complaint raised against your Booking number: {booking_number}'
    html_content = render_to_string('emails/complaint-raised.html', {
        'email': email,
        'name': name,
        'booking_number': booking_number,
        'remarks': remarks
    })
    email_thread = EmailThread(email, subject, html_content)
    email_thread.start()
    return "Complaint email is being sent"


def send_payment_verification_email(email, name, booking_number):
    subject = f'Booking payment is verified'
    html_content = render_to_string('emails/payment-verified.html', {
        'email': email,
        'name': name,
        'booking_number': booking_number
    })
    email_thread = EmailThread(email, subject, html_content)
    email_thread.start()
    return "Payment verification email is being sent"


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
    email_thread = EmailThread(email, subject, html_content)
    email_thread.start()
    return "New order booking email is being sent"


def send_booking_documents_email(email, name, booking_number, document_type):
    subject = f'{document_type} confirmation against Booking number: {booking_number}'
    html_content = render_to_string('emails/documents.html', {
        'email': email,
        'name': name,
        'booking_number': booking_number,
        'document_type': document_type
    })
    email_thread = EmailThread(email, subject, html_content)
    email_thread.start()
    return "Document email is being sent"


def new_user_welcome_email(email, name):
    subject = f'Welcome to the HajjUmrah.co Family!'
    html_content = render_to_string('emails/new-user-welcome.html', {
        'email': email,
        'name': name
    })
    email_thread = EmailThread(email, subject, html_content)
    email_thread.start()
    return "new user welcome email is being sent"


def user_subscribe_email(email):
    subject = f'Subscription Confirmation – Stay Tuned!'
    html_content = render_to_string('emails/subscribe.html', {
        'email': email
    })
    email_thread = EmailThread(email, subject, html_content)
    email_thread.start()
    return "subscribe email is being sent"


def forgot_password_email(email, forgot_link):
    subject = f'Forgot Password Request'
    html_content = render_to_string('emails/forgot-password.html', {
        'email': email,
        'forgot_link': forgot_link
    })
    email_thread = EmailThread(email, subject, html_content)
    email_thread.start()
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
    email_thread = EmailThread(email, subject, html_content)
    email_thread.start()
    return "new user welcome email is being sent"


def preparation_email(email, name, package_type):
    subject = f'Important Checklist for Your {package_type} Journey'
    html_content = render_to_string('emails/prepration_tips.html', {
        'email': email,
        'name': name,
        'package_type': package_type,
    })
    email_thread = EmailThread(email, subject, html_content)
    email_thread.start()
    return "checklist email is being sent"


class CustomPagination(PageNumberPagination):
    page_size = 10  # Default page size
    page_size_query_param = 'page_size'
    max_page_size = 100


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