from celery import shared_task

from scraper.email_engine import scrape_email_source
from scraper.engine import scrape_source
from scraper.notifications import notify_admins_of_pending_events


@shared_task(name="scraper.scrape_source_task")
def scrape_source_task(source_id):
    return scrape_source(source_id)


@shared_task(name="scraper.scrape_email_source_task")
def scrape_email_source_task(source_id):
    return scrape_email_source(source_id)


@shared_task(name="scraper.notify_pending_events_task")
def notify_pending_events_task():
    return notify_admins_of_pending_events()
