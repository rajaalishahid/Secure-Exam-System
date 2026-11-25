from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0003_user_public_id_user_setup_expires_at'),
    ]

    operations = [
        migrations.RemoveField(model_name='user', name='last_totp_step'),
        migrations.RemoveField(model_name='user', name='recovery_hashes'),
        migrations.AlterField(
            model_name='user',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending_activation', 'Pending Activation'),
                    ('active', 'Active'),
                    ('suspended', 'Suspended'),
                ],
                default='pending_activation',
                max_length=30,
            ),
        ),
    ]
