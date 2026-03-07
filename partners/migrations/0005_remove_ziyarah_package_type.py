from django.db import migrations, models


def migrate_ziyarah_packages_to_umrah(apps, schema_editor):
    HuzBasicDetail = apps.get_model("partners", "HuzBasicDetail")
    HuzBasicDetail.objects.filter(package_type="Ziyarah").update(package_type="Umrah")


class Migration(migrations.Migration):

    dependencies = [
        ("partners", "0004_add_hotspot_indexes"),
    ]

    operations = [
        migrations.RunPython(
            migrate_ziyarah_packages_to_umrah,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="huzbasicdetail",
            name="package_type",
            field=models.CharField(
                choices=[("Hajj", "hajj"), ("Umrah", "umrah")],
                max_length=50,
            ),
        ),
    ]
