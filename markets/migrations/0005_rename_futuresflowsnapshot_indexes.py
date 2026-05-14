from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("markets", "0004_futuresflowsnapshot"),
    ]

    operations = [
        migrations.RenameIndex(
            model_name="futuresflowsnapshot",
            new_name="markets_fut_symbol_08041c_idx",
            old_name="markets_fut_symbol_402ae7_idx",
        ),
        migrations.RenameIndex(
            model_name="futuresflowsnapshot",
            new_name="markets_fut_provide_abf71c_idx",
            old_name="markets_fut_provide_99497f_idx",
        ),
    ]
