# Manual edit — replace the fixed active-hours window with a relative work shift.

from django.db import migrations, models


def derive_shift_hours(apps, schema_editor):
    """Backfill work_shift_hours from the old window length (overnight-safe)."""
    SiteConfig = apps.get_model("linkedin", "SiteConfig")
    for cfg in SiteConfig.objects.all():
        duration = (cfg.active_end_hour - cfg.active_start_hour) % 24 or 24
        cfg.work_shift_hours = duration
        cfg.save(update_fields=["work_shift_hours"])


class Migration(migrations.Migration):

    dependencies = [
        ("linkedin", "0015_update_active_hours_4pm_6pm"),
    ]

    operations = [
        migrations.AddField(
            model_name="siteconfig",
            name="work_shift_hours",
            field=models.PositiveSmallIntegerField(
                default=2,
                help_text="Hours the daemon works from the moment it starts; "
                "then it idles until the daemon is restarted.",
            ),
        ),
        migrations.RunPython(derive_shift_hours, reverse_code=migrations.RunPython.noop),
        migrations.RemoveField(model_name="siteconfig", name="active_start_hour"),
        migrations.RemoveField(model_name="siteconfig", name="active_end_hour"),
        migrations.RemoveField(model_name="siteconfig", name="active_timezone"),
        migrations.RemoveField(model_name="siteconfig", name="rest_days"),
    ]
