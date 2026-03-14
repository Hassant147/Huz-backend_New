from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("booking", "0009_bookingairlinedetail_flight_direction"),
    ]

    operations = [
        migrations.AddField(
            model_name="bookinghotelandtransport",
            name="riyadh_name",
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name="bookinghotelandtransport",
            name="riyadh_number",
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
        migrations.AddField(
            model_name="bookinghotelandtransport",
            name="taif_name",
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name="bookinghotelandtransport",
            name="taif_number",
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
    ]
