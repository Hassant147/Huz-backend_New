from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("common", "0001_initial"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="userotp",
            index=models.Index(fields=["phone_number"], name="userotp_phone_idx"),
        ),
        migrations.AddIndex(
            model_name="userprofile",
            index=models.Index(
                fields=["country_code", "phone_number"],
                name="user_country_phone_idx",
            ),
        ),
    ]
