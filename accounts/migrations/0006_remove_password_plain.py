from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0005_remove_user_totp_secret_user_password_plain'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='user',
            name='password_plain',
        ),
    ]
