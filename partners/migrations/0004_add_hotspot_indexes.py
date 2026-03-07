from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("partners", "0003_add_package_date_range_and_optional_nights"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="huzbasicdetail",
            index=models.Index(
                fields=["package_type", "package_status", "start_date"],
                name="huz_pkg_type_status_start_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="huzbasicdetail",
            index=models.Index(
                fields=["package_status", "created_time"],
                name="huz_pkg_status_created_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="huzbasicdetail",
            index=models.Index(
                fields=["package_provider", "created_time"],
                name="huz_pkg_provider_created_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="huzbasicdetail",
            index=models.Index(
                fields=["is_featured", "package_status", "start_date"],
                name="huz_pkg_featured_status_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="huzairlinedetail",
            index=models.Index(
                fields=["airline_for_package", "flight_from"],
                name="huz_air_pkg_from_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="huzairlinedetail",
            index=models.Index(
                fields=["airline_for_package", "flight_to"],
                name="huz_air_pkg_to_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="huzairlinedetail",
            index=models.Index(
                fields=["ticket_type"],
                name="huz_air_ticket_type_idx",
            ),
        ),
    ]
