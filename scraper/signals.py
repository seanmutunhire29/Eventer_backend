from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django_celery_beat.models import IntervalSchedule, PeriodicTask

from core.models import ScrapeSource


def sync_periodic_task(source):
    task_name = f"scrape-source-{source.pk}"
    if not source.is_active:
        PeriodicTask.objects.filter(name=task_name).update(enabled=False)
        return

    schedule, _ = IntervalSchedule.objects.get_or_create(
        every=source.scrape_interval_hours,
        period=IntervalSchedule.HOURS,
    )
    task_path = (
        "scraper.scrape_email_source_task"
        if source.source_type == source.SourceType.EMAIL
        else "scraper.scrape_source_task"
    )
    periodic_task, _ = PeriodicTask.objects.update_or_create(
        name=task_name,
        defaults={
            "interval": schedule,
            "task": task_path,
            "args": f"[{source.pk}]",
            "enabled": source.is_active,
        },
    )
    if source.periodic_task_id != periodic_task.pk:
        ScrapeSource.objects.filter(pk=source.pk).update(periodic_task=periodic_task)


@receiver(post_save, sender=ScrapeSource)
def scrape_source_saved(sender, instance, **kwargs):
    sync_periodic_task(instance)


@receiver(post_delete, sender=ScrapeSource)
def scrape_source_deleted(sender, instance, **kwargs):
    if instance.periodic_task_id:
        PeriodicTask.objects.filter(pk=instance.periodic_task_id).delete()
