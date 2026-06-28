from celery import shared_task

from scraper.engine import scrape_source


@shared_task(name="scraper.scrape_source_task")
def scrape_source_task(source_id):
    return scrape_source(source_id)
