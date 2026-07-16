from django.core.management.base import BaseCommand

from scraper.email_engine import scrape_email_source


class Command(BaseCommand):
    help = "Run an email inbox scrape for a single ScrapeSource by ID"

    def add_arguments(self, parser):
        parser.add_argument("source_id", type=int)

    def handle(self, *args, **options):
        result = scrape_email_source(options["source_id"])
        self.stdout.write(self.style.SUCCESS(str(result)))
