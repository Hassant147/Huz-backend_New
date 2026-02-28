import random
import string
from datetime import timedelta
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction
from django.conf import settings
from django.utils import timezone

from partners.models import (
    HuzAirlineDetail,
    HuzBasicDetail,
    HuzHotelImage,
    HuzHotelDetail,
    HuzPackageDateRange,
    HuzTransportDetail,
    HuzZiyarahDetail,
    PartnerProfile,
)


AIRLINES = [
    "PIA",
    "Saudia",
    "Qatar Airways",
    "Emirates",
    "Flynas",
    "AirBlue",
]

FLIGHT_CITIES = [
    ("Karachi", "Makkah", "Jeddah", "Karachi"),
    ("Lahore", "Madinah", "Jeddah", "Lahore"),
    ("Islamabad", "Makkah", "Jeddah", "Islamabad"),
    ("Peshawar", "Madinah", "Jeddah", "Islamabad"),
]

TRANSPORT_TYPES = [
    ("Car", "Private"),
    ("Coaster", "Shared"),
    ("Bus", "Luxury"),
    ("Hiace", "Private"),
]

HOTEL_POOL = {
    "Makkah": [
        ("Dar Al Tawhid Intercontinental", "5 Star", "Minutes Walk"),
        ("Swissotel Makkah", "5 Star", "Minutes Walk"),
        ("Anjum Hotel", "4 Star", "KM"),
        ("Makkah Clock Royal Tower Fairmont", "5 Star", "Minutes Walk"),
        ("Movenpick Hajar Tower Makkah", "5 Star", "Minutes Walk"),
        ("Hilton Suites Makkah", "5 Star", "Minutes Walk"),
        ("Pullman Zamzam Makkah", "5 Star", "Minutes Walk"),
        ("Le Meridien Towers Makkah", "4 Star", "KM"),
        ("Al Kiswah Towers", "3 Star", "KM"),
        ("Emaar Grand Hotel Makkah", "4 Star", "KM"),
    ],
    "Madinah": [
        ("Sofitel Shahd Al Madinah", "5 Star", "KM"),
        ("Pullman Zamzam Madina", "5 Star", "KM"),
        ("Dallah Taibah", "4 Star", "KM"),
        ("Anwar Al Madinah Movenpick", "5 Star", "KM"),
        ("Madinah Hilton", "5 Star", "KM"),
        ("Dar Al Iman Intercontinental", "5 Star", "KM"),
        ("Frontel Al Harithia", "4 Star", "KM"),
        ("Saja Al Madinah", "4 Star", "KM"),
        ("Al Aqeeq Madinah Hotel", "5 Star", "KM"),
    ],
    "Jeddah": [
        ("Movenpick Hotel City Star", "5 Star", "KM"),
        ("Centro Shaheen", "4 Star", "KM"),
        ("Radisson Blu Jeddah", "5 Star", "KM"),
        ("Crowne Plaza Jeddah", "5 Star", "KM"),
        ("InterContinental Jeddah", "5 Star", "KM"),
        ("Novotel Jeddah Tahlia", "4 Star", "KM"),
        ("Ibis Jeddah City Center", "3 Star", "KM"),
        ("Sheraton Jeddah Hotel", "5 Star", "KM"),
        ("Park Inn by Radisson Jeddah", "4 Star", "KM"),
    ],
    "Taif": [
        ("Awaliv International", "5 Star", "KM"),
        ("Iris Boutique Taif", "4 Star", "KM"),
        ("Le Meridien Al Hada", "5 Star", "KM"),
        ("Remaj Hotel", "4 Star", "KM"),
        ("Velar Inn Hotel", "3 Star", "KM"),
    ],
    "Riyadh": [
        ("Braira Qurtubah", "5 Star", "KM"),
        ("Executives Olaya Hotel", "4 Star", "KM"),
        ("Hilton Riyadh Hotel", "5 Star", "KM"),
        ("Rosh Rayhaan by Rotana", "5 Star", "KM"),
        ("Novotel Suites Riyadh Olaya", "4 Star", "KM"),
    ],
}

ZIYARAH_MAKKAH = [
    "Masjid Aisha",
    "Jabal al-Nour",
    "Cave of Hira",
    "Jannat al-Mualla",
    "Mina",
    "Arafat",
    "Muzdalifah",
]

ZIYARAH_MADINAH = [
    "Quba Mosque",
    "Masjid Al-Qiblatain",
    "Jannat Al-Baqi",
    "Uhud Mountain",
    "Masjid Ghamamah",
    "Battlefield of Khandaq",
]


def random_phone():
    return "+92" + "".join(random.choices(string.digits, k=10))


class Command(BaseCommand):
    help = "Seed realistic random Hajj/Umrah/Ziyarah packages with linked details."

    def add_arguments(self, parser):
        parser.add_argument(
            "--count",
            type=int,
            default=10,
            help="Number of packages to create (default: 10)",
        )
        parser.add_argument(
            "--partner-session-token",
            type=str,
            default=None,
            help="Optional existing partner_session_token to attach seeded packages.",
        )
        parser.add_argument(
            "--status",
            type=str,
            default="Active",
            choices=["Initialize", "Completed", "NotActive", "Active", "Deactivated", "Block", "Pending"],
            help="Package status for seeded records (default: Active).",
        )
        parser.add_argument(
            "--min-distinct-hotels",
            type=int,
            default=0,
            help="Ensure at least this many distinct hotel names in this seed run.",
        )

    def _get_or_create_partner(self, partner_session_token):
        if partner_session_token:
            partner = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
            if not partner:
                raise ValueError(f"No partner found with token: {partner_session_token}")
            return partner

        partner = PartnerProfile.objects.filter(account_status="Active").order_by("created_time").first()
        if partner:
            return partner

        partner = PartnerProfile.objects.order_by("created_time").first()
        if partner:
            return partner

        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
        now_stamp = timezone.now().strftime("%Y%m%d%H%M%S")
        return PartnerProfile.objects.create(
            partner_session_token=f"seed_partner_{now_stamp}_{suffix}",
            user_name=f"seed_partner_{suffix}",
            email=f"seed+{suffix}@huz.local",
            name="Seed Partner",
            partner_type="Company",
            sign_type="Email",
            phone_number=random_phone(),
            account_status="Active",
            is_email_verified=True,
            is_phone_verified=True,
        )

    @staticmethod
    def _random_package_type():
        return random.choice(["Umrah", "Hajj", "Ziyarah"])

    def _build_name(self, package_type, nights):
        tier = random.choice(["Economy", "Value", "Premium", "Executive"])
        month = random.choice(["Muharram", "Shaban", "Ramadan", "Shawwal", "Dhul Hijjah"])
        return f"{package_type} {tier} {nights}N - {month} 2026"

    def _create_basic_detail(self, partner, package_status):
        package_type = self._random_package_type()

        mecca_nights = random.randint(5, 9) if package_type in {"Umrah", "Hajj"} else random.randint(2, 5)
        madinah_nights = random.randint(3, 6)
        jeddah_nights = random.randint(0, 2)
        taif_nights = random.randint(0, 1)
        riyadah_nights = random.randint(0, 1)
        total_nights = mecca_nights + madinah_nights + jeddah_nights + taif_nights + riyadah_nights

        base_cost = random.randint(175000, 540000)
        start_date = timezone.now() + timedelta(days=random.randint(7, 180))
        end_date = start_date + timedelta(days=total_nights)

        token_suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        huz_token = f"SEED-{timezone.now().strftime('%Y%m%d%H%M%S')}-{token_suffix}"

        package = HuzBasicDetail.objects.create(
            huz_token=huz_token,
            package_type=package_type,
            package_name=self._build_name(package_type, total_nights),
            package_base_cost=float(base_cost),
            cost_for_child=float(round(base_cost * random.uniform(0.6, 0.8))),
            cost_for_infants=float(random.randint(25000, 65000)),
            cost_for_sharing=float(base_cost),
            cost_for_quad=float(base_cost + random.randint(15000, 35000)),
            cost_for_triple=float(base_cost + random.randint(35000, 65000)),
            cost_for_double=float(base_cost + random.randint(75000, 125000)),
            cost_for_single=float(base_cost + random.randint(180000, 280000)),
            discount_if_child_with_bed=float(random.randint(5000, 28000)),
            mecca_nights=mecca_nights,
            madinah_nights=madinah_nights,
            jeddah_nights=jeddah_nights,
            taif_nights=taif_nights,
            riyadah_nights=riyadah_nights,
            start_date=start_date,
            end_date=end_date,
            description=(
                "Comfort-focused package with guided support, reliable transfers, "
                "and curated hotel stays for families and small groups."
            ),
            is_visa_included=random.choice([True, True, True, False]),
            is_airport_reception_included=random.choice([True, False]),
            is_tour_guide_included=True,
            is_insurance_included=random.choice([True, True, False]),
            is_breakfast_included=True,
            is_lunch_included=random.choice([True, False]),
            is_dinner_included=random.choice([True, False]),
            is_package_open_for_other_date=random.choice([True, False]),
            package_validity=end_date - timedelta(days=random.randint(1, 5)),
            package_status=package_status,
            package_stage=5,
            package_provider=partner,
        )
        return package

    @staticmethod
    def _create_date_ranges(package):
        for _ in range(random.randint(2, 4)):
            shift_days = random.randint(0, 14)
            start = package.start_date + timedelta(days=shift_days)
            end = package.end_date + timedelta(days=shift_days)
            HuzPackageDateRange.objects.create(
                start_date=start,
                end_date=end,
                group_capacity=random.choice([5, 10, 15, 20, 30, 40, 50]),
                package_validity=end - timedelta(days=random.randint(1, 4)),
                date_range_for_package=package,
            )

    @staticmethod
    def _create_airline(package):
        flight_from, flight_to, return_from, return_to = random.choice(FLIGHT_CITIES)
        HuzAirlineDetail.objects.create(
            airline_name=random.choice(AIRLINES),
            ticket_type=random.choice(["economy", "economy", "business"]),
            flight_from=flight_from,
            flight_to=flight_to,
            return_flight_from=return_from,
            return_flight_to=return_to,
            is_return_flight_included=True,
            airline_for_package=package,
        )

    @staticmethod
    def _create_transport(package):
        transport_name, transport_type = random.choice(TRANSPORT_TYPES)
        HuzTransportDetail.objects.create(
            transport_name=transport_name,
            transport_type=transport_type,
            routes="MKK_MDN,MKK_JED,MDN_MKK,MDN_JED,JED_MKK,JED_MDN",
            transport_for_package=package,
        )

    def _pick_hotel_for_city(self, city):
        city_pool = HOTEL_POOL[city]
        unused = [item for item in city_pool if item[0] not in self._used_hotel_names]
        selected = random.choice(unused if unused else city_pool)
        self._used_hotel_names.add(selected[0])
        return selected

    def _create_hotel_images(self, hotel):
        if not self._hotel_image_paths:
            return
        images = random.sample(
            self._hotel_image_paths,
            k=min(random.randint(3, 5), len(self._hotel_image_paths)),
        )
        for index, image_path in enumerate(images, start=1):
            HuzHotelImage.objects.create(
                hotel_image=image_path,
                sort_order=index,
                image_for_hotel=hotel,
            )

    def _create_hotels(self, package):
        night_map = {
            "Makkah": package.mecca_nights,
            "Madinah": package.madinah_nights,
            "Jeddah": package.jeddah_nights,
            "Taif": package.taif_nights,
            "Riyadh": package.riyadah_nights,
        }
        for city, nights in night_map.items():
            if nights <= 0:
                continue
            hotel_name, rating, distance_type = self._pick_hotel_for_city(city)
            distance = str(random.choice([3, 5, 7, 10, 15, 25])) if distance_type == "KM" else str(random.choice([5, 7, 10, 12]))
            hotel = HuzHotelDetail.objects.create(
                hotel_city=city,
                hotel_name=hotel_name,
                hotel_rating=rating,
                room_sharing_type=random.choice(["Double", "Triple", "Quad"]),
                hotel_distance=distance,
                distance_type=distance_type,
                is_shuttle_services_included=random.choice([True, False]),
                is_air_condition=True,
                is_television=True,
                is_wifi=True,
                is_elevator=True,
                is_attach_bathroom=True,
                is_washroom_amenities=True,
                is_english_toilet=True,
                is_indian_toilet=random.choice([True, False]),
                is_laundry=random.choice([True, False]),
                hotel_for_package=package,
            )
            self._create_hotel_images(hotel)

    @staticmethod
    def _create_ziyarah(package):
        if package.package_type == "Ziyarah":
            places = random.sample(ZIYARAH_MAKKAH + ZIYARAH_MADINAH, k=8)
        else:
            places = random.sample(ZIYARAH_MAKKAH, k=4) + random.sample(ZIYARAH_MADINAH, k=4)
        HuzZiyarahDetail.objects.create(
            ziyarah_list=",".join(places),
            ziyarah_for_package=package,
        )

    @transaction.atomic
    def handle(self, *args, **options):
        count = options["count"]
        package_status = options["status"]
        partner_token = options.get("partner_session_token")
        min_distinct_hotels = max(0, options.get("min_distinct_hotels", 0))

        if count <= 0:
            self.stdout.write(self.style.WARNING("Nothing to seed: --count must be > 0"))
            return

        self._used_hotel_names = set()
        hotel_images_dir = Path(settings.MEDIA_ROOT) / "hotel_images"
        self._hotel_image_paths = []
        if hotel_images_dir.exists():
            for ext in ("*.jpg", "*.jpeg", "*.png", "*.webp", "*.avif"):
                for file_path in hotel_images_dir.glob(ext):
                    self._hotel_image_paths.append(f"hotel_images/{file_path.name}")

        partner = self._get_or_create_partner(partner_token)
        created_tokens = []
        created_count = 0
        guard = 0
        while created_count < count or len(self._used_hotel_names) < min_distinct_hotels:
            guard += 1
            if guard > count + 150:
                break
            package = self._create_basic_detail(partner, package_status)
            self._create_date_ranges(package)
            self._create_airline(package)
            self._create_transport(package)
            self._create_hotels(package)
            self._create_ziyarah(package)
            created_tokens.append(package.huz_token)
            created_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Seed complete. Created {len(created_tokens)} packages for partner {partner.partner_session_token}."
            )
        )
        self.stdout.write(f"Distinct hotels in this run: {len(self._used_hotel_names)}")
        for token in created_tokens:
            self.stdout.write(f"- {token}")
