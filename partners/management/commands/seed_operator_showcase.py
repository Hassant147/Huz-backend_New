import secrets

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from common.utility import hash_password
from partners.models import (
    BusinessProfile,
    HuzBasicDetail,
    PartnerBankAccount,
    PartnerMailingDetail,
    PartnerProfile,
    PartnerServices,
    Wallet,
)
from partners.partner_profile import build_unique_partner_session_token


class Command(BaseCommand):
    help = (
        "Create or update a realistic operator company account and seed packages "
        "for that operator."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            default="alnoor.demo.operator@hajjumrah.org",
            help="Operator login email.",
        )
        parser.add_argument(
            "--username",
            default="alnoor_demo_ops",
            help="Operator username.",
        )
        parser.add_argument(
            "--password",
            default="",
            help="Operator password. Leave blank to auto-generate one.",
        )
        parser.add_argument(
            "--name",
            default="Faisal Qureshi",
            help="Operator display name.",
        )
        parser.add_argument(
            "--phone",
            default="+923001234567",
            help="Operator phone number in +<countrycode><10-digit> format.",
        )
        parser.add_argument(
            "--company-name",
            default="Al Noor Hajj & Umrah Services",
            help="Business name for the operator profile.",
        )
        parser.add_argument(
            "--count",
            type=int,
            default=6,
            help="How many packages to seed for this operator.",
        )
        parser.add_argument(
            "--package-status",
            default="Active",
            choices=[
                "Initialize",
                "Completed",
                "NotActive",
                "Active",
                "Deactivated",
                "Block",
                "Pending",
            ],
            help="Status for seeded packages.",
        )
        parser.add_argument(
            "--wallet-amount",
            type=float,
            default=125000.0,
            help="Starting wallet balance for the seeded operator.",
        )

    @staticmethod
    def _generate_password():
        return f"HuzOp!{secrets.token_urlsafe(9)}A7"

    @staticmethod
    def _generate_wallet_code():
        return f"WALLET-{secrets.token_hex(12).upper()}"

    @staticmethod
    def _normalize_phone(phone):
        normalized = f"{phone or ''}".strip().replace(" ", "")
        if not normalized.startswith("+"):
            normalized = f"+{normalized}"
        if len(normalized) < 11:
            raise CommandError(
                "Phone number must include country code and a 10-digit local number."
            )
        return normalized

    @staticmethod
    def _split_phone(phone):
        normalized = Command._normalize_phone(phone)
        return normalized[:-10], normalized[-10:]

    def _get_existing_partner(self, email):
        return PartnerProfile.objects.filter(email__iexact=email).first()

    def _validate_username(self, username, partner=None):
        username = f"{username or ''}".strip().lower()
        if not username:
            raise CommandError("Username is required.")

        username_owner = PartnerProfile.objects.filter(user_name=username).first()
        if username_owner and (partner is None or username_owner.partner_id != partner.partner_id):
            raise CommandError(f"Username already exists: {username}")
        return username

    @transaction.atomic
    def _create_or_update_partner(self, options):
        email = f"{options['email'] or ''}".strip().lower()
        if not email:
            raise CommandError("Email is required.")

        partner = self._get_existing_partner(email)
        username = self._validate_username(options["username"], partner=partner)
        country_code, local_phone = self._split_phone(options["phone"])
        raw_password = f"{options['password'] or ''}".strip() or self._generate_password()

        if partner is None:
            partner = PartnerProfile(
                partner_session_token=build_unique_partner_session_token(email),
                email=email,
                name=options["name"].strip(),
                user_name=username,
                partner_type="Company",
                sign_type="Email",
                country_code=country_code,
                phone_number=local_phone,
                password=hash_password(raw_password),
                is_phone_verified=True,
                is_email_verified=True,
                is_address_exist=True,
                account_status="Active",
                otp="",
            )
            partner.save()
            created = True
        else:
            partner.email = email
            partner.name = options["name"].strip()
            partner.user_name = username
            partner.partner_type = "Company"
            partner.sign_type = "Email"
            partner.country_code = country_code
            partner.phone_number = local_phone
            partner.password = hash_password(raw_password)
            partner.is_phone_verified = True
            partner.is_email_verified = True
            partner.is_address_exist = True
            partner.account_status = "Active"
            if not partner.partner_session_token:
                partner.partner_session_token = build_unique_partner_session_token(email)
            partner.save()
            created = False

        wallet, wallet_created = Wallet.objects.get_or_create(
            wallet_session=partner,
            defaults={
                "wallet_code": self._generate_wallet_code(),
                "wallet_amount": options["wallet_amount"],
            },
        )
        if not wallet_created and wallet.wallet_amount <= 0:
            wallet.wallet_amount = options["wallet_amount"]
            wallet.save(update_fields=["wallet_amount", "last_update_time"])

        PartnerServices.objects.update_or_create(
            services_of_partner=partner,
            defaults={
                "is_hajj_service_offer": True,
                "is_umrah_service_offer": True,
                "is_ziyarah_service_offer": True,
                "is_transport_service_offer": True,
                "is_visa_service_offer": True,
            },
        )

        BusinessProfile.objects.update_or_create(
            company_of_partner=partner,
            defaults={
                "company_name": options["company_name"].strip(),
                "contact_name": options["name"].strip(),
                "contact_number": self._normalize_phone(options["phone"]),
                "company_website": "https://www.alnoorhajj.com",
                "license_type": "IATA / Ministry Approved",
                "license_number": "ANHUPK-2026-117",
                "total_experience": "12 years",
                "company_bio": (
                    "Trusted Pakistan-based Hajj and Umrah operator offering "
                    "family, executive, and group departures with guided support, "
                    "reliable transport, and curated hotel stays."
                ),
            },
        )

        PartnerMailingDetail.objects.update_or_create(
            mailing_of_partner=partner,
            defaults={
                "street_address": "Office 12, 3rd Floor, Rehmat Plaza, Shahrah-e-Faisal",
                "address_line2": "Near Nursery Bus Stop",
                "city": "Karachi",
                "state": "Sindh",
                "country": "Pakistan",
                "postal_code": "75400",
                "lat": "24.8719",
                "long": "67.0832",
            },
        )

        PartnerBankAccount.objects.get_or_create(
            bank_account_for_partner=partner,
            account_title=options["company_name"].strip(),
            account_number="7860102456789012",
            bank_name="Meezan Bank",
            branch_code="0176",
        )

        return partner, raw_password, created

    def handle(self, *args, **options):
        partner, raw_password, created = self._create_or_update_partner(options)

        existing_package_count = HuzBasicDetail.objects.filter(
            package_provider=partner
        ).count()

        package_count = max(0, options["count"])
        if package_count:
            call_command(
                "seed_huz_packages",
                count=package_count,
                partner_session_token=partner.partner_session_token,
                status=options["package_status"],
                min_distinct_hotels=min(10, max(4, package_count)),
            )

        final_package_count = HuzBasicDetail.objects.filter(
            package_provider=partner
        ).count()
        created_package_count = final_package_count - existing_package_count

        self.stdout.write(
            self.style.SUCCESS(
                f"{'Created' if created else 'Updated'} operator account "
                f"{partner.email} ({partner.partner_session_token})."
            )
        )
        self.stdout.write(f"Username: {partner.user_name}")
        self.stdout.write(f"Password: {raw_password}")
        self.stdout.write(f"Wallet balance: {options['wallet_amount']:.2f}")
        self.stdout.write(f"Packages created in this run: {created_package_count}")
        self.stdout.write(f"Total packages for operator: {final_package_count}")
