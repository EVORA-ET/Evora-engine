from django.db import migrations


def seed(apps, schema_editor):
    from ev_migration.catalogue.loader import load
    load(apps.get_model('ev_migration', 'EVModel'))


class Migration(migrations.Migration):

    dependencies = [('ev_migration', '0005_ev_catalogue')]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
