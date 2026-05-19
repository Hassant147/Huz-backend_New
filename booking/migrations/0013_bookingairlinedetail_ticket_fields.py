from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("booking", "0012_remove_passportvalidity_report_rabbit_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="bookingairlinedetail",
            name="airline_name",
            field=models.CharField(blank=True, max_length=150, null=True),
        ),
        migrations.AddField(
            model_name="bookingairlinedetail",
            name="flight_number",
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
        migrations.AddField(
            model_name="bookingairlinedetail",
            name="pnr",
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
        migrations.AddField(
            model_name="bookingairlinedetail",
            name="baggage_note",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="bookingairlinedetail",
            name="route_note",
            field=models.TextField(blank=True, null=True),
        ),
    ]
