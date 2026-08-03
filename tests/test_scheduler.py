from __future__ import annotations

from tg_summariser.config import settings
from tg_summariser.scheduler import build_scheduler


class DummyBot:
    pass


class DummyTelegramClient:
    pass


def test_scheduler_registers_article_import_jobs_separately(monkeypatch) -> None:
    monkeypatch.setattr(settings, "tgarticles_import_schedules", "08:30,11:30,14:30,17:30,20:30")
    monkeypatch.setattr(settings, "digest_schedules", "09:00")
    settings.__dict__.pop("tgarticles_import_times", None)
    settings.__dict__.pop("digest_times", None)

    scheduler = build_scheduler(DummyBot(), DummyTelegramClient())

    job_ids = {job.id for job in scheduler.get_jobs()}

    assert {
        "tgarticles-import-08:30",
        "tgarticles-import-11:30",
        "tgarticles-import-14:30",
        "tgarticles-import-17:30",
        "tgarticles-import-20:30",
    }.issubset(job_ids)
    assert "digest-09:00" in job_ids
