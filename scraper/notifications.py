from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from core.models import Event


def notify_admins_of_pending_events():
    """Runs hourly. Emails the admin a summary only when there's genuinely
    new stuff to review — events created by the email scraper that haven't
    been flagged before. Never re-sends about the same event twice."""
    pending = Event.objects.filter(
        review_status=Event.ReviewStatus.PENDING, admin_notified_at__isnull=True
    ).select_related("building").order_by("start_time")

    if not pending.exists():
        return {"status": "skipped", "message": "no new pending events"}

    if not settings.ADMIN_NOTIFICATION_EMAIL:
        return {"status": "failed", "message": "ADMIN_NOTIFICATION_EMAIL is not set in .env"}

    lines = [
        f"{pending.count()} new event(s) extracted from email are waiting for review:",
        "",
    ]
    for event in pending:
        location = event.building.official_name if event.building else (event.unresolved_location or "unknown location")
        lines.append(
            f"- {event.event_name}\n"
            f"  {event.start_time.strftime('%a %b %d, %Y %I:%M %p')} @ {location}\n"
            f"  category: {event.get_category_display()}"
        )
    lines.append("")
    lines.append(f"Review them at {settings.SITE_URL}/admin-frontend/#review")

    send_mail(
        subject=f"Eventer: {pending.count()} event(s) pending review",
        message="\n".join(lines),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[settings.ADMIN_NOTIFICATION_EMAIL],
    )

    notified_ids = list(pending.values_list("id", flat=True))
    Event.objects.filter(id__in=notified_ids).update(admin_notified_at=timezone.now())

    return {"status": "sent", "count": len(notified_ids)}
