from django.db import models


class ProcessedEmail(models.Model):
    """Tracks which inbox messages have already been run through extraction,
    so re-scraping the same mailbox never re-processes (or re-bills) the same
    email twice."""

    scrape_source = models.ForeignKey(
        "core.ScrapeSource", on_delete=models.CASCADE, related_name="processed_emails"
    )
    message_id = models.CharField(max_length=998, db_index=True)
    subject = models.CharField(max_length=500, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    events_extracted = models.PositiveIntegerField(default=0)
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [["scrape_source", "message_id"]]
        ordering = ["-processed_at"]

    def __str__(self):
        return f"{self.subject} ({self.message_id})"
