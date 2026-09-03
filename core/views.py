from django.http import FileResponse, Http404
from django.shortcuts import render

from .models import AppRelease


def latest_release():
    return AppRelease.objects.filter(is_published=True).first()


def landing(request):
    release = latest_release()
    return render(
        request,
        "public/index.html",
        {
            "release": release,
            "has_android": bool(release and release.has_android),
            "has_ios": bool(release and release.has_ios),
        },
    )


def download_android(request):
    release = latest_release()
    if not release or not release.android_apk:
        raise Http404("Android build is not available yet.")
    return FileResponse(
        release.android_apk.open("rb"),
        as_attachment=True,
        filename="Eventer.apk",
    )


def download_ios(request):
    release = latest_release()
    if not release or not release.ios_ipa:
        raise Http404("iOS build is not available yet.")
    return FileResponse(
        release.ios_ipa.open("rb"),
        as_attachment=True,
        filename="Eventer.ipa",
    )
