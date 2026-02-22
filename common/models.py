from django.db import models
import uuid
from django.utils import timezone


class UserOTP(models.Model):
    # Primary key for the model, a unique identifier generated automatically using UUID
    otp_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Field to store the phone number associated with the OTP
    phone_number = models.CharField(max_length=15, null=False)
    # Field to store the OTP password, can be null (e.g., if not generated yet)
    otp_password = models.CharField(max_length=10, null=True)
    # Timestamp indicating when the OTP was created, defaults to the current time
    created_time = models.DateTimeField(auto_now=True)

    def __str__(self):
        return str(self.otp_id)


class SubscribeUser(models.Model):
    sub_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.CharField(max_length=50)

    def __str__(self):
        return self.email


class UserProfile(models.Model):
    # Choices for gender with additional options
    GENDER_CHOICES = [
        ('male', 'Male'),
        ('female', 'Female'),
        ('non_binary', 'Non-binary'),
        ('prefer_not_to_say', 'Prefer not to say'),
        ('other', 'Other')
    ]

    # Choices for user type field
    USER_CHOICES = [
        ('user', 'User'),
        ('customer', 'Customer'),
        ('admin', 'Admin'),
        ('sales_director', 'Sales_Director')
    ]

    # Primary key for the model, a unique identifier generated automatically using UUID
    user_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Field to store session token, unique for each session, must required
    session_token = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=100)
    country_code = models.CharField(max_length=5, null=False)
    phone_number = models.CharField(max_length=15, null=False)
    email = models.CharField(max_length=50)
    user_gender = models.CharField(max_length=20, choices=GENDER_CHOICES, null=True)
    user_type = models.CharField(max_length=50, choices=USER_CHOICES)

    # Field to store the Firebase token for mobile notifications, can be null
    firebase_token = models.CharField(max_length=500, null=True, default='')
    # Field to store the Firebase token for web notifications, can be null
    web_firebase_token = models.CharField(max_length=500, null=True, default='')
    # Boolean field indicating if the phone number is verified, defaults to False
    is_phone_verified = models.BooleanField(null=True, default=False)
    # Boolean field indicating if the email is verified, defaults to False
    is_email_verified = models.BooleanField(null=True, default=False)
    is_address_exist = models.BooleanField(null=True, default=False)
    email_otp = models.CharField(max_length=10, null=True)
    otp_time = models.DateTimeField(auto_now=True)
    # Boolean field indicating if notifications are allowed, defaults to True
    is_notification_allowed = models.BooleanField(null=True, default=True)
    # Field to store the user's profile photo, can be null and left blank
    user_photo = models.ImageField(upload_to='user_images', null=True, blank=True)
    # Field to store the account status, can be null
    account_status = models.CharField(max_length=10, null=True)
    # Timestamp indicating when the OTP was created, defaults to the current time
    created_time = models.DateTimeField(default=timezone.now)
    online = models.BooleanField(default=False)
    last_seen = models.DateTimeField(null=True)

    def __str__(self):
        return self.session_token


class Wallet(models.Model):
    # Auto-incrementing primary key for the wallet
    wallet_id = models.AutoField(primary_key=True)
    # One unique number or code associated with the wallet, required to be unique
    wallet_code = models.CharField(max_length=100, unique=True)
    wallet_amount = models.FloatField(default=0.0)

    # Foreign key linking the wallet to a user profile
    # related_name='wallet_session' allows reverse querying from UserProfile to Wallet
    # on_delete=models.CASCADE means the wallet will be deleted if the linked UserProfile is deleted
    wallet_session = models.ForeignKey(UserProfile, related_name='wallet_session', on_delete=models.CASCADE)
    last_update_time = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.wallet_code} - {self.wallet_session}"


class ManageNotification(models.Model):
    notification_id = models.AutoField(auto_created=True, primary_key=True)
    # Title of the notification, required
    notification_title = models.CharField(max_length=200)
    # Message content of the notification, required
    notification_message = models.TextField(max_length=500, null=True)
    # Indicates if the notification has been seen, defaults to False
    is_seen = models.BooleanField(default=False)
    firebase_token = models.CharField(max_length=500, null=True)
    web_firebase_token = models.CharField(max_length=500, null=True)

    # Foreign key linking the notification to a user profile
    # related_name='notification_session' allows reverse querying from UserProfile to ManageNotification
    # on_delete=models.CASCADE means the notification will be deleted if the linked UserProfile is deleted
    notification_for_user = models.ForeignKey(UserProfile, related_name='notification_session', on_delete=models.CASCADE)

    # If Notification for booking, its can be null and defaults to an empty string
    notification_for_booking = models.CharField(max_length=200, null=True, default='')
    notification_date = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.notification_title} - {self.notification_for_user}"


class MailingDetail(models.Model):
    # Primary key for the mailing address, a unique identifier generated automatically using UUID
    address_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # First line of the street address
    street_address = models.CharField(max_length=300)
    # Second line of the address, can be blank or null
    address_line2 = models.CharField(max_length=300, blank=True, null=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100, null=True, blank=True)
    country = models.CharField(max_length=100)
    postal_code = models.CharField(max_length=10, null=True, blank=True)
    # Latitude of the address location, can be blank or null
    lat = models.CharField(max_length=50, null=True, blank=True)
    # Longitude of the address location, can be blank or null
    long = models.CharField(max_length=50, null=True, blank=True)

    # Foreign key linking the mailing address to a user profile
    # related_name='mailing_session' allows reverse querying from UserProfile to MailingDetail
    # on_delete=models.CASCADE means the mailing detail will be deleted if the linked UserProfile is deleted
    mailing_session = models.ForeignKey(UserProfile, related_name='mailing_session', on_delete=models.CASCADE)

    def __str__(self):
        return f"{self.street_address} - Token: {self.mailing_session}"


class UserBankAccount(models.Model):
    # Primary key for the bank account, a unique identifier generated automatically using UUID
    account_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account_title = models.CharField(max_length=100, unique=False)
    account_number = models.CharField(max_length=100)
    bank_name = models.CharField(max_length=100, unique=False)
    branch_code = models.CharField(max_length=100, unique=False)
    created_time = models.DateTimeField(auto_now=True)

    # Foreign key linking the bank account to a user profile
    # related_name='bank_account_for_user' allows reverse querying from UserProfile to UserBankAccount
    # on_delete=models.CASCADE means the bank account details will be deleted if the linked UserProfile is deleted
    bank_account_for_user = models.ForeignKey(UserProfile, related_name='bank_account_for_user', on_delete=models.CASCADE)

    def __str__(self):
        return "%s %s" % (self.account_id, self.bank_account_for_user)


class UserTransactionHistory(models.Model):
    # Primary key for the transaction history entry, a unique identifier generated automatically using UUID
    transaction_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Transaction Code or reference associated with the transaction
    transaction_code = models.CharField(max_length=100)
    transaction_amount = models.FloatField(default=0.0)
    # Type of transaction (e.g., credit, deposit, withdrawal, purchase)
    transaction_type = models.CharField(max_length=50)
    transaction_time = models.DateTimeField(default=timezone.now)

    # Foreign key linking the transaction to a user profile
    # related_name='transaction_user_token' allows reverse querying from UserProfile to UserTransactionHistory
    # on_delete=models.CASCADE means the transaction history will be deleted if the linked UserProfile is deleted
    transaction_for_user = models.ForeignKey(UserProfile, related_name='transaction_user_token', on_delete=models.CASCADE)

    # Foreign key linking the transaction to a wallet
    # related_name='transaction_wallet_token' allows reverse querying from Wallet to UserTransactionHistory
    # on_delete=models.CASCADE means the transaction history will be deleted if the linked Wallet is deleted
    transaction_wallet_token = models.ForeignKey(Wallet, related_name='transaction_wallet_token', on_delete=models.CASCADE)
    transaction_description = models.CharField(max_length=200, null=True, blank=True)
    # If this transaction associated with the Package, it can be blank or null
    transaction_for_package = models.CharField(max_length=100, null=True, blank=True)

    def __str__(self):
        return f"{self.transaction_code} {self.transaction_for_user}"


class UserWithdraw(models.Model):
    # Primary key for the withdrawal request, a unique identifier generated automatically using UUID
    withdraw_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Foreign key linking the withdrawal request to a user profile
    # related_name='withdraw_for_user' allows reverse querying from UserProfile to UserWithdraw
    # on_delete=models.SET_NULL means the withdraw_for_user field will be set to NULL if the linked Profile is deleted
    withdraw_for_user = models.ForeignKey(UserProfile, related_name='withdraw_for_user', on_delete=models.SET_NULL, null=True)

    # Foreign key linking the withdrawal request to a bank account
    # related_name='withdraw_bank' allows reverse querying from UserBankAccount to UserWithdraw
    # on_delete=models.SET_NULL means the withdraw_bank field will be set to NULL if the linked Bank Account is deleted
    withdraw_bank = models.ForeignKey(UserBankAccount, related_name='withdraw_bank', on_delete=models.SET_NULL, null=True)
    withdraw_amount = models.FloatField()
    request_time = models.DateTimeField(auto_now=True)
    # Status of the withdrawal request (e.g., pending, processed, declined), can be null
    withdraw_status = models.CharField(max_length=20, unique=False, null=True)
    process_time = models.DateTimeField(null=True)
    withdraw_backup_detail = models.TextField(null=True)

    def __str__(self):
        return "%s %s" % (self.withdraw_amount, self.request_time)