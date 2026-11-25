from django.db import migrations


def forward(apps, schema_editor):
    """Convert any legacy status values to the simplified active/suspended model."""
    User = apps.get_model('accounts', 'User')
    legacy_map = {
        'pending_activation': 'active',
        'pending_mfa': 'active',
    }
    for old_status, new_status in legacy_map.items():
        User.objects.filter(status=old_status).update(status=new_status)


def reverse(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    User.objects.filter(status='active').update(status='pending_activation')


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0008_accounttoken_created_at_accounttoken_delivery_method_and_more'),
    ]
    operations = [
        migrations.RunPython(forward, reverse),
    ]