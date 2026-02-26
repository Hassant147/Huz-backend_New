from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("partners", "0002_sync_hotel_image_schema"),
    ]

    operations = [
        migrations.AddField(
            model_name="huzbasicdetail",
            name="discount_if_child_with_bed",
            field=models.FloatField(default=0.0),
        ),
        migrations.AddField(
            model_name="huzbasicdetail",
            name="jeddah_nights",
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name="huzbasicdetail",
            name="riyadah_nights",
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name="huzbasicdetail",
            name="taif_nights",
            field=models.IntegerField(default=0),
        ),
        migrations.CreateModel(
            name="HuzPackageDateRange",
            fields=[
                (
                    "range_id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("start_date", models.DateTimeField()),
                ("end_date", models.DateTimeField()),
                ("group_capacity", models.IntegerField(blank=True, null=True)),
                ("package_validity", models.DateTimeField(blank=True, null=True)),
                (
                    "date_range_for_package",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="package_date_ranges",
                        to="partners.huzbasicdetail",
                    ),
                ),
            ],
        ),
    ]

