from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("partners", "0005_remove_ziyarah_package_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="businessprofile",
            name="mobile_number",
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
        migrations.AddField(
            model_name="businessprofile",
            name="ntn",
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
        migrations.AddField(
            model_name="businessprofile",
            name="official_email",
            field=models.EmailField(blank=True, max_length=254, null=True),
        ),
        migrations.AddField(
            model_name="businessprofile",
            name="operator_type",
            field=models.CharField(blank=True, max_length=40, null=True),
        ),
        migrations.AddField(
            model_name="businessprofile",
            name="owner_cnic",
            field=models.CharField(blank=True, max_length=30, null=True),
        ),
    ]
