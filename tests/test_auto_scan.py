import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import bot
from config import settings


class AutoScanTests(unittest.TestCase):
    def make_application(self, jobs=None):
        job_queue = Mock()
        job_queue.get_jobs_by_name.return_value = jobs or []
        application = SimpleNamespace(
            job_queue=job_queue,
            bot=SimpleNamespace(send_message=AsyncMock()),
        )
        return application, job_queue

    def test_auto_scan_schedules_job_on_startup(self):
        application, job_queue = self.make_application()
        with patch.object(settings, "AUTO_SCAN", True):
            asyncio.run(bot.post_init(application))

        job_queue.run_repeating.assert_called_once_with(
            bot.scan_job,
            interval=settings.SCAN_INTERVAL_MIN * 60,
            first=10,
            name="scan",
            chat_id=settings.ADMIN_ID,
        )

    def test_auto_scan_does_not_duplicate_existing_job(self):
        application, job_queue = self.make_application(jobs=[Mock()])
        with patch.object(settings, "AUTO_SCAN", True):
            asyncio.run(bot.post_init(application))

        job_queue.run_repeating.assert_not_called()

    def test_auto_scan_is_off_by_default(self):
        application, job_queue = self.make_application()
        with patch.object(settings, "AUTO_SCAN", False):
            asyncio.run(bot.post_init(application))

        job_queue.run_repeating.assert_not_called()


if __name__ == "__main__":
    unittest.main()
