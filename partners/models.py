from django.db import models
import uuid
from django.utils import timezone
from common.models import UserProfile


class PartnerProfile(models.Model):
    TYPE_CHOICES = [('NA', 'na'), ('Individual', 'individual'), ('Company', 'company')]
    ACCOUNT_STATUS_CHOICES = [
        ('Active', 'active'),
        ('Pending', 'pending'),
        ('Rejected', 'rejected'),
        ('Deactivate', 'deactivate'),
        ('Block', 'block')
    ]
    SIGN_TYPE_CHOICES = [('Gmail', 'gmail'), ('Apple', 'apple'), ('Email', 'email')]

    # Unique identifier for the partner
    partner_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Unique token for the partner
    partner_session_token = models.CharField(max_length=100, unique=True, null=True)
    # Unique username for the partner
    user_name = models.CharField(max_length=100, unique=True, null=True)
    email = models.EmailField(max_length=254, blank=True, null=True)
    name = models.CharField(max_length=100)
    # Type of the partner (e.g., Individual, Company, Admin)
    partner_type = models.CharField(max_length=50, choices=TYPE_CHOICES, null=True)
    # Sign-in type (e.g., Gmail, Apple, Email)
    sign_type = models.CharField(max_length=50, choices=SIGN_TYPE_CHOICES, null=True)
    # Password for the partner's account
    password = models.CharField(max_length=128, blank=True, null=True)
    # Country code for the partner's phone number
    country_code = models.CharField(max_length=5, blank=True, null=True)
    # Phone number of the partner
    phone_number = models.CharField(max_length=15, blank=True, null=True)
    otp = models.CharField(max_length=10, blank=True, null=True)
    otp_time = models.DateTimeField(auto_now=True)
    is_phone_verified = models.BooleanField(default=False)
    is_email_verified = models.BooleanField(default=False)
    is_address_exist = models.BooleanField(default=False)
    firebase_token = models.CharField(max_length=500, null=True)
    web_firebase_token = models.CharField(max_length=700, null=True)
    user_photo = models.ImageField(upload_to='user_images', blank=True, null=True)
    # Account status (e.g., Active, Pending, Rejected, Deactivate, Block)
    account_status = models.CharField(max_length=50, choices=ACCOUNT_STATUS_CHOICES, null=True, default="Pending")
    created_time = models.DateTimeField(default=timezone.now)
    # ForeignKey linking to the UserProfile, representing a sales agent token
    sales_agenet_token = models.ForeignKey(UserProfile, related_name='sales_agenet_token', on_delete=models.SET_NULL, null=True)
    online = models.BooleanField(default=False)
    last_seen = models.DateTimeField(null=True)

    def __str__(self):
        return self.partner_session_token


class PasswordResetToken(models.Model):
    partner = models.ForeignKey(PartnerProfile, on_delete=models.CASCADE)
    token = models.UUIDField(default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    is_used = models.BooleanField(default=False)

    def __str__(self):
        return f"Password Reset Token for {self.partner.email}"


class Wallet(models.Model):
    # Auto-incrementing primary key for the wallet
    wallet_id = models.AutoField(primary_key=True)
    # One unique number or code associated with the wallet, required to be unique
    wallet_code = models.CharField(max_length=100, unique=True)
    wallet_amount = models.FloatField(default=0.0)

    # Foreign key linking the wallet to a partner profile
    # related_name='wallet_session' allows reverse querying from PartnerProfile to Wallet
    # on_delete=models.CASCADE means the wallet will be deleted if the linked PartnerProfile is deleted
    wallet_session = models.ForeignKey(PartnerProfile, related_name='wallet_session', on_delete=models.CASCADE)
    last_update_time = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.wallet_code} - {self.wallet_session}"


class PartnerServices(models.Model):
    # Auto-incrementing primary key for the PartnerServices
    service_id = models.AutoField(primary_key=True)
    is_hajj_service_offer = models.BooleanField(default=False)
    is_umrah_service_offer = models.BooleanField(default=False)
    is_ziyarah_service_offer = models.BooleanField(default=False)
    is_transport_service_offer = models.BooleanField(default=False)
    is_visa_service_offer = models.BooleanField(default=False)

    # Foreign key linking the PartnerServices to a partner profile
    # related_name='services_of_partner' allows reverse querying from PartnerProfile to Partner Service
    # on_delete=models.CASCADE means the wallet will be deleted if the linked PartnerProfile is deleted
    services_of_partner = models.ForeignKey(PartnerProfile, related_name='services_of_partner', on_delete=models.CASCADE)

    def __str__(self):
        return f"{self.service_id} - {self.services_of_partner}"


class IndividualProfile(models.Model):
    # Auto-incrementing primary key for the Individual Partner Profile
    individual_id = models.AutoField(primary_key=True)
    contact_name = models.CharField(max_length=100, blank=True, null=True)
    contact_number = models.CharField(max_length=15, blank=True, null=True)
    driving_license_number = models.CharField(max_length=50, blank=True, null=True)
    front_side_photo = models.ImageField(upload_to='user_images', blank=True, null=True)
    back_side_photo = models.ImageField(upload_to='user_images', blank=True, null=True)

    # Foreign key linking the IndividualProfile to a partner profile
    # related_name='individual_profile_of_partner' allows reverse querying from PartnerProfile to Individual Profile
    # on_delete=models.CASCADE means the IndividualProfile will be deleted if the linked PartnerProfile is deleted
    individual_profile_of_partner = models.ForeignKey(PartnerProfile, related_name='individual_profile_of_partner', on_delete=models.CASCADE)

    def __str__(self):
        return f"{self.contact_name} - {self.individual_profile_of_partner}"


class BusinessProfile(models.Model):
    # Unique identifier for the company
    company_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company_name = models.CharField(max_length=100, blank=True, null=True)
    contact_name = models.CharField(max_length=100, blank=True, null=True)
    contact_number = models.CharField(max_length=15, blank=True, null=True)
    company_website = models.CharField(max_length=50, blank=True, null=True)
    license_type = models.CharField(max_length=30, blank=True, null=True)
    license_number = models.CharField(max_length=30, blank=True, null=True)
    total_experience = models.CharField(max_length=30, blank=True, null=True)
    # Brief bio or description of the company
    company_bio = models.TextField(null=True)
    # Logo of the company
    company_logo = models.ImageField(upload_to='user_images', blank=True, null=True)
    # license Certificate image for the company
    license_certificate = models.FileField(upload_to='user_images', blank=True, null=True)
    # ForeignKey linking to the PartnerProfile, representing the partner associated with the company
    company_of_partner = models.ForeignKey(PartnerProfile, related_name='company_of_partner', on_delete=models.CASCADE)

    def __str__(self):
        return f"{self.company_name} - {self.company_of_partner}"


class PartnerMailingDetail(models.Model):
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

    # Foreign key linking the mailing address to a Partner profile
    # related_name='mailing_of_partner' allows reverse querying from PartnerProfile to PartnerMailingDetail
    # on_delete=models.CASCADE means the mailing detail will be deleted if the linked PartnerProfile is deleted
    mailing_of_partner = models.ForeignKey(PartnerProfile, related_name='mailing_of_partner', on_delete=models.CASCADE)

    def __str__(self):
        return f"{self.street_address} - Token: {self.mailing_of_partner}"


class PartnerBankAccount(models.Model):
    # Primary key for the bank account, a unique identifier generated automatically using UUID
    account_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account_title = models.CharField(max_length=100, unique=False)
    account_number = models.CharField(max_length=100)
    bank_name = models.CharField(max_length=100, unique=False)
    branch_code = models.CharField(max_length=100, unique=False)
    created_time = models.DateTimeField(auto_now=True)

    # Foreign key linking the bank account to a user profile
    # related_name='bank_account_for_partner' allows reverse querying from PartnerProfile to UserBankAccount
    # on_delete=models.CASCADE means the bank account details will be deleted if the linked PartnerProfile is deleted
    bank_account_for_partner = models.ForeignKey(PartnerProfile, related_name='bank_account_for_partner', on_delete=models.CASCADE)

    def __str__(self):
        return "%s %s" % (self.account_id, self.bank_account_for_partner)


class PartnerTransactionHistory(models.Model):
    # Primary key for the transaction history entry, a unique identifier generated automatically using UUID
    transaction_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Transaction Code or reference associated with the transaction
    transaction_code = models.CharField(max_length=100)
    transaction_amount = models.FloatField(default=0.0)
    # Type of transaction (e.g., credit, deposit, withdrawal, purchase)
    transaction_type = models.CharField(max_length=50)
    transaction_time = models.DateTimeField(default=timezone.now)

    # Foreign key linking the transaction to a user profile
    # related_name='transaction_for_partner' allows reverse querying from UserProfile to PartnerTransactionHistory
    # on_delete=models.CASCADE means the transaction history will be deleted if the linked PartnerProfile is deleted
    transaction_for_partner = models.ForeignKey(PartnerProfile, related_name='transaction_for_partner', on_delete=models.CASCADE)

    # Foreign key linking the transaction to a wallet
    # related_name='transaction_wallet_token' allows reverse querying from Wallet to PartnerTransactionHistory
    # on_delete=models.CASCADE means the transaction history will be deleted if the linked Wallet is deleted
    transaction_wallet_token = models.ForeignKey(Wallet, related_name='transaction_wallet_token', on_delete=models.CASCADE)
    transaction_description = models.CharField(max_length=200, null=True, blank=True)
    # If this transaction associated with the Package, it can be blank or null
    transaction_for_package = models.CharField(max_length=100, null=True, blank=True)

    def __str__(self):
        return f"{self.transaction_code} {self.transaction_for_partner}"


class PartnerWithdraw(models.Model):
    # Primary key for the withdrawal request, a unique identifier generated automatically using UUID
    withdraw_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Foreign key linking the withdrawal request to a user profile
    # related_name='withdraw_for_partner' allows reverse querying from PartnerProfile to UserWithdraw
    # on_delete=models.SET_NULL means the withdraw_for_partner field will be set to NULL if linked Profile is deleted
    withdraw_for_partner = models.ForeignKey(PartnerProfile, related_name='withdraw_for_partner', on_delete=models.SET_NULL, null=True)

    # Foreign key linking the withdrawal request to a bank account
    # related_name='withdraw_bank' allows reverse querying from PartnerBankAccount to PartnerWithdraw
    # on_delete=models.SET_NULL means the withdraw_bank field will be set to NULL if the linked Bank Account is deleted
    withdraw_bank = models.ForeignKey(PartnerBankAccount, related_name='withdraw_bank', on_delete=models.SET_NULL, null=True)
    withdraw_amount = models.FloatField()
    request_time = models.DateTimeField(auto_now=True)
    # Status of the withdrawal request (e.g., pending, processed, declined), can be null
    withdraw_status = models.CharField(max_length=20, unique=False, null=True)
    process_time = models.DateTimeField(null=True)
    withdraw_backup_detail = models.TextField(null=True)

    def __str__(self):
        return "%s %s" % (self.withdraw_amount, self.request_time)


class HuzBasicDetail(models.Model):
    # Define package type choices
    PACKAGE_TYPE_CHOICES = [
        ('Hajj', 'hajj'),
        ('Umrah', 'umrah'),
        ('Ziyarah', 'ziyarah')
    ]

    # Define package status choices
    PACKAGE_STATUS_CHOICES = [
        ('Initialize', 'initialize'),
        ('Completed', 'completed'),
        ('NotActive', 'notActive'),
        ('Active', 'active'),
        ('Deactivated', 'deactivated'),
        ('Block', 'block'),
        ('Pending', 'pending')
    ]

    # Unique identifier for each Huz package
    huz_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    huz_token = models.CharField(max_length=100, unique=True, null=True)
    # Type of the package
    package_type = models.CharField(max_length=50, choices=PACKAGE_TYPE_CHOICES)
    package_name = models.CharField(max_length=100)

    # Base cost of the package
    package_base_cost = models.FloatField(default=0.0)

    # Additional costs based on different criteria
    cost_for_child = models.FloatField(default=0.0)
    cost_for_infants = models.FloatField(default=0.0)
    cost_for_sharing = models.FloatField(default=0.0)
    cost_for_quad = models.FloatField(default=0.0)
    cost_for_triple = models.FloatField(default=0.0)
    cost_for_double = models.FloatField(default=0.0)
    cost_for_single = models.FloatField(default=0.0)

    # Number of nights in Mecca and Madinah
    mecca_nights = models.IntegerField(default=0)
    madinah_nights = models.IntegerField(default=0)

    # Start date of the package
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()

    # Description of the package
    description = models.TextField(blank=True)

    # Boolean fields to indicate inclusions in the package
    is_visa_included = models.BooleanField(null=True, default=False)
    is_airport_reception_included = models.BooleanField(null=True, default=False)
    is_tour_guide_included = models.BooleanField(null=True, default=False)
    is_insurance_included = models.BooleanField(null=True, default=False)
    is_breakfast_included = models.BooleanField(null=True, default=False)
    is_lunch_included = models.BooleanField(null=True, default=False)
    is_dinner_included = models.BooleanField(null=True, default=False)
    is_package_open_for_other_date = models.BooleanField(null=True, default=False)

    # Indicates if the package is featured
    is_featured = models.BooleanField(null=True, default=False)
    package_validity = models.DateTimeField(null=True)

    # Status of the package
    package_status = models.CharField(max_length=50, choices=PACKAGE_STATUS_CHOICES)

    # Stage of the package
    package_stage = models.IntegerField(default=0)

    # Time when the package was created
    created_time = models.DateTimeField(default=timezone.now)

    # Reference to the partner providing the package
    package_provider = models.ForeignKey(PartnerProfile, related_name='package_provider', on_delete=models.CASCADE)

    def __str__(self):
        return f"{self.huz_token} - {self.package_provider}"


class HuzAirlineDetail(models.Model):
    airline_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Name of the airline
    airline_name = models.CharField(max_length=100)
    # Type of ticket (e.g., economy, business)
    ticket_type = models.CharField(max_length=100)

    # Departure location for the flight
    flight_from = models.CharField(max_length=100, null=True)
    # Destination location for the flight
    flight_to = models.CharField(max_length=100, null=True)

    # Departure location for the flight
    return_flight_from = models.CharField(max_length=100, null=True)
    # Destination location for the flight
    return_flight_to = models.CharField(max_length=100, null=True)
    # Indicates if a return flight is included in the package
    is_return_flight_included = models.BooleanField(null=True, default=False)
    # Foreign key to link the airline detail to a specific HuzBasicDetail package
    airline_for_package = models.ForeignKey(HuzBasicDetail, related_name='airline_for_package', on_delete=models.CASCADE)

    def __str__(self):
        return f"{self.airline_name} - {self.airline_for_package}"


class HuzTransportDetail(models.Model):
    transport_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Name of the transport (e.g., bus, car, train)
    transport_name = models.CharField(max_length=100)
    # Type of transport (e.g., luxury, standard)
    transport_type = models.CharField(max_length=100)

    # Routes covered by the transport (optional)
    routes = models.CharField(max_length=250, null=True)
    # Foreign key to link the transport detail to a specific HuzBasicDetail package
    transport_for_package = models.ForeignKey(HuzBasicDetail, related_name='transport_for_package',
                                              on_delete=models.CASCADE)

    def __str__(self):
        return f"{self.transport_name} - {self.transport_for_package}"


class HuzHotelDetail(models.Model):
    hotel_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    hotel_city = models.CharField(max_length=100)
    hotel_name = models.CharField(max_length=100)

    # Rating of the hotel (e.g., 3-star, 5-star)
    hotel_rating = models.CharField(max_length=50)

    # Type of room sharing (e.g., single, double, quad)
    room_sharing_type = models.CharField(max_length=50)

    # Distance from a specific point of interest (optional)
    hotel_distance = models.CharField(max_length=50, null=True)

    # Unit of distance (e.g., km, miles) (optional)
    distance_type = models.CharField(max_length=20, null=True)

    # Indicates services
    is_shuttle_services_included = models.BooleanField(null=True, default=False)
    is_air_condition = models.BooleanField(null=True, default=False)
    is_television = models.BooleanField(null=True, default=False)
    is_wifi = models.BooleanField(null=True, default=False)
    is_elevator = models.BooleanField(null=True, default=False)
    is_attach_bathroom = models.BooleanField(null=True, default=False)
    is_washroom_amenities = models.BooleanField(null=True, default=False)
    is_english_toilet = models.BooleanField(null=True, default=False)
    is_indian_toilet = models.BooleanField(null=True, default=False)
    is_laundry = models.BooleanField(null=True, default=False)

    # Foreign key to link the hotel detail to a specific HuzBasicDetail package
    hotel_for_package = models.ForeignKey(HuzBasicDetail, related_name='hotel_for_package', on_delete=models.CASCADE)

    def __str__(self):
        # Return a string representation of the hotel detail
        return f"{self.hotel_name} - {self.hotel_for_package}"


class HuzZiyarahDetail(models.Model):
    ziyarah_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ziyarah_list = models.TextField(blank=True)
    # Foreign key to link the ziyarat detail to a specific HuzBasicDetail package
    ziyarah_for_package = models.ForeignKey(HuzBasicDetail, related_name='ziyarah_for_package', on_delete=models.CASCADE)

    def __str__(self):
        return self.ziyarah_list


# class TransportPackages(models.Model):
#     transport_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
#     # Unique token for the transport package (optional)
#     transport_token = models.CharField(max_length=100, unique=True, null=True)
#
#     # Type of package (optional)
#     package_type = models.CharField(max_length=50, null=True)
#
#     # Type of transport (e.g., bus, car, van)
#     transport_type = models.CharField(max_length=100)
#
#     # Name and model of the vehicle
#     name_and_model = models.CharField(max_length=100)
#
#     # Vehicle's plate number
#     plate_no = models.CharField(max_length=100)
#
#     # Sitting capacity of the vehicle
#     sitting_capacity = models.CharField(max_length=100)
#
#     # Photos of the vehicle (optional)
#     vehicle_photos = models.ImageField(upload_to='user_images', blank=True, null=True)
#
#     # Availability status of the transport package
#     availability = models.CharField(max_length=200)
#
#     # Additional common field 1
#     common_1 = models.CharField(max_length=100)
#
#     # Additional common field 2
#     common_2 = models.CharField(max_length=100)
#
#     # Cost of the transport package
#     cost = models.FloatField(default=0.0)
#
#     # Time when the transport package was created
#     created_time = models.DateTimeField(default=timezone.now)
#
#     # Status of the transport package (optional)
#     package_status = models.CharField(max_length=50, null=True)
#
#     # Foreign key to link the transport package to a specific PartnerProfile
#     transport_package_provider = models.ForeignKey(PartnerProfile, related_name='transport_package_provider', on_delete=models.CASCADE)
#
#     def __str__(self):
#         return f"{self.transport_token} - {self.transport_package_provider}"
