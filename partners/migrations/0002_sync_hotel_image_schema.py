from django.db import migrations


def ensure_hotel_image_schema(apps, schema_editor):
    connection = schema_editor.connection
    introspection = connection.introspection

    HuzHotelDetail = apps.get_model("partners", "HuzHotelDetail")
    HuzHotelImage = apps.get_model("partners", "HuzHotelImage")

    detail_table = HuzHotelDetail._meta.db_table
    image_table = HuzHotelImage._meta.db_table

    table_names = set(introspection.table_names())
    if detail_table not in table_names:
        return

    catalog_field = HuzHotelDetail._meta.get_field("catalog_hotel")

    with connection.cursor() as cursor:
        detail_columns = {
            column.name
            for column in introspection.get_table_description(cursor, detail_table)
        }

    if catalog_field.column not in detail_columns:
        schema_editor.add_field(HuzHotelDetail, catalog_field)

    table_names = set(introspection.table_names())
    if image_table not in table_names:
        schema_editor.create_model(HuzHotelImage)


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ("partners", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(ensure_hotel_image_schema, migrations.RunPython.noop),
    ]
