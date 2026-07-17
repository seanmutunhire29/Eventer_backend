import uuid

from django.db import models


class Building(models.Model):
    official_name = models.CharField(max_length=255)
    lat = models.FloatField()
    lng = models.FloatField()
    geojson_id = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["official_name"]

    def __str__(self):
        return self.official_name


class BuildingAlias(models.Model):
    class Source(models.TextChoices):
        SCRAPER = "scraper", "Scraper"
        ADMIN = "admin", "Admin"
        STUDENT_REPORT = "student_report", "Student Report"

    building = models.ForeignKey(
        Building, on_delete=models.CASCADE, related_name="aliases"
    )
    alias = models.CharField(max_length=255, db_index=True)
    source = models.CharField(
        max_length=100, choices=Source.choices, default=Source.SCRAPER
    )

    class Meta:
        unique_together = [["building", "alias"]]
        verbose_name_plural = "building aliases"

    def __str__(self):
        return f"{self.alias} → {self.building.official_name}"


class ScrapeSource(models.Model):
    class ScrapeStatus(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        PARTIAL = "partial", "Partial"

    url = models.URLField(max_length=500)
    label = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    scrape_interval_hours = models.PositiveIntegerField(default=3)
    last_scraped_at = models.DateTimeField(null=True, blank=True)
    last_scrape_status = models.CharField(
        max_length=50, choices=ScrapeStatus.choices, blank=True
    )
    last_scrape_log = models.TextField(blank=True)
    selector_config = models.JSONField(default=dict)
    periodic_task = models.OneToOneField(
        "django_celery_beat.PeriodicTask",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="scrape_source",
    )

    class Meta:
        ordering = ["label"]

    def __str__(self):
        return self.label


class Event(models.Model):
    class Category(models.TextChoices):
        ACADEMIC_LECTURE = "academic_lecture", "Academic / Lecture"
        SOCIAL_PARTY = "social_party", "Social / Party"
        FREE_FOOD = "free_food", "Free Food"
        SPORTS_ATHLETICS = "sports_athletics", "Sports / Athletics"
        ARTS_PERFORMANCE = "arts_performance", "Arts / Performance"
        CAREER_PROFESSIONAL = "career_professional", "Career / Professional"
        CLUB_ORG_MEETING = "club_org_meeting", "Club / Org Meeting"
        RELIGIOUS_SPIRITUAL = "religious_spiritual", "Religious / Spiritual"
        VOLUNTEER_COMMUNITY = "volunteer_community", "Volunteer / Community"
        HEALTH_WELLNESS = "health_wellness", "Health / Wellness"

    CATEGORY_COLORS = {
        Category.ACADEMIC_LECTURE: "Dartmouth Green",
        Category.SOCIAL_PARTY: "Purple",
        Category.FREE_FOOD: "Orange",
        Category.SPORTS_ATHLETICS: "Red",
        Category.ARTS_PERFORMANCE: "Pink",
        Category.CAREER_PROFESSIONAL: "Blue",
        Category.CLUB_ORG_MEETING: "Teal",
        Category.RELIGIOUS_SPIRITUAL: "Gold",
        Category.VOLUNTEER_COMMUNITY: "Lime",
        Category.HEALTH_WELLNESS: "Mint",
    }

    CATEGORY_ALIASES = {
        "food": Category.FREE_FOOD,
        "free food": Category.FREE_FOOD,
        "academic": Category.ACADEMIC_LECTURE,
        "lectures & seminars": Category.ACADEMIC_LECTURE,
        "lectures and seminars": Category.ACADEMIC_LECTURE,
        "social": Category.SOCIAL_PARTY,
        "sports": Category.SPORTS_ATHLETICS,
        "athletics & recreation": Category.SPORTS_ATHLETICS,
        "athletics and recreation": Category.SPORTS_ATHLETICS,
        "arts": Category.ARTS_PERFORMANCE,
        "performances": Category.ARTS_PERFORMANCE,
        "exhibitions": Category.ARTS_PERFORMANCE,
        "films": Category.ARTS_PERFORMANCE,
        "career": Category.CAREER_PROFESSIONAL,
        "workshops & training": Category.CAREER_PROFESSIONAL,
        "workshops and training": Category.CAREER_PROFESSIONAL,
        "conferences": Category.CAREER_PROFESSIONAL,
        "club": Category.CLUB_ORG_MEETING,
        "clubs & organizations": Category.CLUB_ORG_MEETING,
        "clubs and organizations": Category.CLUB_ORG_MEETING,
        "religious": Category.RELIGIOUS_SPIRITUAL,
        "spiritual & worship": Category.RELIGIOUS_SPIRITUAL,
        "spiritual and worship": Category.RELIGIOUS_SPIRITUAL,
        "volunteer": Category.VOLUNTEER_COMMUNITY,
        "service & volunteer": Category.VOLUNTEER_COMMUNITY,
        "service and volunteer": Category.VOLUNTEER_COMMUNITY,
        "health": Category.HEALTH_WELLNESS,
        "wellness": Category.HEALTH_WELLNESS,
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_name = models.CharField(max_length=255)
    building = models.ForeignKey(
        Building, on_delete=models.SET_NULL, null=True, blank=True, related_name="events"
    )
    unresolved_location = models.CharField(max_length=255, null=True, blank=True)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    description = models.TextField(blank=True)
    category = models.CharField(max_length=50, choices=Category.choices)
    other_info = models.JSONField(default=dict, blank=True)
    source_url = models.URLField(max_length=500, blank=True)
    scrape_source = models.ForeignKey(
        ScrapeSource,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)
    is_verified = models.BooleanField(default=False)
    missed_scrape_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["start_time"]
        constraints = [
            models.UniqueConstraint(
                fields=["event_name", "building", "start_time"],
                name="unique_event_dedup",
            )
        ]

    def __str__(self):
        return self.event_name
