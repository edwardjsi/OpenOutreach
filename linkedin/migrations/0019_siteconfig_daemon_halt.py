# Generated manually for the circuit-breaker (checkpoint halt) feature.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('linkedin', '0018_campaign_search_geo_urn'),
    ]

    operations = [
        migrations.AddField(
            model_name='siteconfig',
            name='daemon_halt',
            field=models.BooleanField(default=False, help_text='When set, the daemon idles without making any LinkedIn request. Set automatically when a security checkpoint is detected; clear it after solving the challenge to resume.'),
        ),
        migrations.AddField(
            model_name='siteconfig',
            name='daemon_halt_reason',
            field=models.TextField(blank=True, default='', help_text='Why the daemon was halted (set automatically on checkpoint).'),
        ),
    ]
