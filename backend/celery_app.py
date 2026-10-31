"""
Unified Celery application configuration
Select desktop or server config based on environment variables
"""

import os

IS_DESKTOP = os.getenv("AUTOCLIP_DESKTOP_MODE") == "1"

if IS_DESKTOP:
    # Use lightweight Celery with filesystem broker/sqlite backend for desktop mode only
    from .desktop_celery import celery_app  # noqa: F401
else:
    # Server/dev mode: Redis or your configured broker/backend
    from celery import Celery

    broker_url = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    backend_url = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

    celery_app = Celery(__name__, broker=broker_url, backend=backend_url)
    celery_app.conf.update(
        task_serializer='json',
        accept_content=['json'],
        result_serializer='json',
        timezone='Asia/Shanghai',
        enable_utc=True,
        task_always_eager=False,  # Server mode async execution
        task_eager_propagates=True,
        result_expires=3600,
        task_ignore_result=False,
        task_routes={
            'backend.tasks.processing.*': {'queue': 'processing'},
            'backend.tasks.video.*': {'queue': 'video'},
            'backend.tasks.notification.*': {'queue': 'notification'},
            'backend.tasks.maintenance.*': {'queue': 'maintenance'},
            'backend.tasks.upload.*': {'queue': 'upload'},
        },
    )

# 自动发现任务
celery_app.autodiscover_tasks([
    'backend.tasks.processing',
    'backend.tasks.video', 
    'backend.tasks.notification',
    'backend.tasks.maintenance',
    'backend.tasks.upload'
])

if __name__ == '__main__':
    celery_app.start()

