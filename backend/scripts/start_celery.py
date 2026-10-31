#!/usr/bin/env python3
"""
Celery startup script
Starts the Celery Worker and Beat scheduler
"""

import os
import sys
import subprocess
import signal
import time
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

def start_celery_worker():
    """Start the Celery Worker"""
    print("🚀 Starting Celery Worker...")
    
    cmd = [
        "celery", "-A", "backend.core.celery_app", "worker",
        "--loglevel=info",
        "--concurrency=2",
        "--queues=processing,video,notification,maintenance",
        "--hostname=worker1@%h"
    ]
    
    try:
        process = subprocess.Popen(cmd, cwd=str(project_root))
        print(f"✅ Celery Worker has started (PID: {process.pid})")
        return process
    except Exception as e:
        print(f"❌ Failed to start Celery Worker: {e}")
        return None

def start_celery_beat():
    """Start the Celery Beat scheduler"""
    print("⏰ Starting Celery Beat scheduler...")
    
    cmd = [
        "celery", "-A", "backend.core.celery_app", "beat",
        "--loglevel=info",
        "--schedule=/tmp/celerybeat-schedule",
        "--pidfile=/tmp/celerybeat.pid"
    ]
    
    try:
        process = subprocess.Popen(cmd, cwd=str(project_root))
        print(f"✅ Celery Beat has started (PID: {process.pid})")
        return process
    except Exception as e:
        print(f"❌ Failed to start Celery Beat: {e}")
        return None

def start_flower():
    """Start the Flower monitoring UI"""
    print("🌸 Starting Flower monitoring UI...")
    
    cmd = [
        "celery", "-A", "backend.core.celery_app", "flower",
        "--port=5555",
        "--loglevel=info"
    ]
    
    try:
        process = subprocess.Popen(cmd, cwd=str(project_root))
        print(f"✅ Flower has started (PID: {process.pid})")
        print("🌐 Flower monitoring UI: http://localhost:5555")
        return process
    except Exception as e:
        print(f"❌ Failed to start Flower: {e}")
        return None

def signal_handler(signum, frame):
    """Signal handler function"""
    print("\n🛑 Stop signal received, shutting down services...")
    sys.exit(0)

def main():
    """Main function"""
    print("🎯 AutoClip Celery Task Queue Launcher")
    print("=" * 50)
    
    # Set up signal handling
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Check Redis connection
    try:
        import redis
        r = redis.Redis.from_url('redis://localhost:6379/0')
        r.ping()
        print("✅ Redis connection is normal")
    except Exception as e:
        print(f"❌ Redis connection failed: {e}")
        print("Please make sure the Redis service is running: redis-server")
        return
    
    # Start services
    processes = []
    
    # Start Worker
    worker_process = start_celery_worker()
    if worker_process:
        processes.append(worker_process)
    
    # Start Beat
    beat_process = start_celery_beat()
    if beat_process:
        processes.append(beat_process)
    
    # Start Flower
    flower_process = start_flower()
    if flower_process:
        processes.append(flower_process)
    
    if not processes:
        print("❌ No services were started successfully")
        return
    
    print("\n🎉 All services have started!")
    print("📊 Service status:")
    print("   - Celery Worker: Processes tasks")
    print("   - Celery Beat: Scheduled task scheduling")
    print("   - Flower: Task monitoring UI (http://localhost:5555)")
    print("\nPress Ctrl+C to stop all services")
    
    try:
        # Wait on the processes
        while True:
            time.sleep(1)
            # Check whether the processes are still running
            for process in processes:
                if process.poll() is not None:
                    print(f"⚠️  Process {process.pid} has exited")
    except KeyboardInterrupt:
        print("\n🛑 Stopping services...")
    finally:
        # Stop all processes
        for process in processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                print(f"🛑 Process {process.pid} has stopped")

if __name__ == "__main__":
    main()