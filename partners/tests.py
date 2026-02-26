from datetime import timedelta

from django.apps import apps
from django.db import connection
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIRequestFactory, APITransactionTestCase

from .models import HuzBasicDetail, PartnerProfile
from .package_management_operator import GetHuzPackageDetailByTokenView


def ensure_tables_for_apps(app_labels):
    existing_tables = set(connection.introspection.table_names())
    pending_models = []
    for app_label in app_labels:
        pending_models.extend(list(apps.get_app_config(app_label).get_models()))

    while pending_models:
        created_in_pass = False
        remaining_models = []

        with connection.schema_editor(atomic=False) as schema_editor:
            for model in pending_models:
                table_name = model._meta.db_table
                if table_name in existing_tables:
                    continue

                try:
                    schema_editor.create_model(model)
                    existing_tables.add(table_name)
                    created_in_pass = True
                except Exception:
                    remaining_models.append(model)

        if not created_in_pass:
            if not remaining_models:
                break
            unresolved_tables = [model._meta.db_table for model in remaining_models]
            raise RuntimeError(
                f"Unable to create tables for test setup: {', '.join(unresolved_tables)}"
            )

        pending_models = remaining_models


class PackageManagementOperatorViewTests(APITransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ensure_tables_for_apps(["common", "partners", "booking"])

    def setUp(self):
        self.factory = APIRequestFactory()
        self.partner = PartnerProfile.objects.create(
            partner_session_token="partner-package-session-token",
            user_name="partner-package-user",
            name="Package Partner",
            partner_type="Company",
            account_status="Active",
        )

        start_date = timezone.now() + timedelta(days=10)
        end_date = start_date + timedelta(days=7)
        self.package = HuzBasicDetail.objects.create(
            huz_token="package-huz-token-001",
            package_type="Hajj",
            package_name="Package Test",
            start_date=start_date,
            end_date=end_date,
            description="Package description",
            package_status="Active",
            package_provider=self.partner,
        )

    def test_get_package_detail_returns_single_item_list(self):
        request = self.factory.get(
            "/partner/get_package_detail_by_partner_token/",
            {
                "partner_session_token": self.partner.partner_session_token,
                "huz_token": self.package.huz_token,
            },
        )

        response = GetHuzPackageDetailByTokenView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0].get("huz_token"), self.package.huz_token)

    def test_get_package_detail_returns_404_for_unknown_token(self):
        request = self.factory.get(
            "/partner/get_package_detail_by_partner_token/",
            {
                "partner_session_token": self.partner.partner_session_token,
                "huz_token": "unknown-token",
            },
        )

        response = GetHuzPackageDetailByTokenView.as_view()(request)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data.get("message"), "Package do not exist.")
