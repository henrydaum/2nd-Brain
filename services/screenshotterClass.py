import time
import os
from pathlib import Path
import threading
import logging
from datetime import datetime
from ctypes import windll, Structure, c_long, byref, sizeof
from PIL import ImageGrab, Image, ImageChops, ImageStat

logger = logging.getLogger("Screenshotter")

BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = Path(os.getenv('LOCALAPPDATA')) / "2nd Brain"

class Screenshotter:
    def __init__(self, config):
        self.config = config
        self.loaded = False
        self.thread = None
        self._stop_event = threading.Event()

        self.DIFF_THRESHOLD = 2 
        self.last_image_thumb = None
        self.last_cleanup_time = 0

    def load(self):
        """Starts the screenshot loop in a separate thread."""
        if self.loaded:
            return

        logger.info("Starting Screenshotter...")
        self.loaded = True
        self._stop_event.clear()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def unload(self):
        """Stops the screenshot loop safely."""
        if not self.loaded:
            return
            
        logger.info("Stopping Screenshotter...")
        self.loaded = False
        self._stop_event.set()
        if self.thread:
            self.thread.join(timeout=2.0)
            self.thread = None

    def toggle(self):
        """Toggles the service on/off."""
        # Fixed logic: If loaded, we want to unload, and vice versa.
        if self.loaded:
            self.unload()
        else:
            self.load()

    def _loop(self):
        while self.loaded and not self._stop_event.is_set():
            try:
                # 1. Capture
                # logger.info("Taking Screenshot...")
                self.take_screenshot()
                
                # 2. Cleanup (Maintenance)
                # logger.info("Cleaning up old screenshots...")
                self.cleanup_old_screenshots()
                
                # 3. Wait
                interval = self.config.get('screenshot_interval', 60)
                # Check stop event every 1s so we can exit quickly
                for _ in range(int(interval)):
                    if self._stop_event.is_set(): break
                    time.sleep(1)
                    
            except Exception as e:
                logger.error(f"Screenshot error: {e}")
                time.sleep(5) # Backoff on error

    def take_screenshot(self):
        """Screenshots the active display and saves it if it is different from the last image."""
        if self.config.get('screenshot_folder', 'Screenshots'):
            save_dir = self.config.get('screenshot_folder', os.path.join(DATA_DIR, 'Screenshots'))
        else:
            save_dir = os.path.join(DATA_DIR, 'Screenshots')
        
        # Ensure dir exists
        if not os.path.exists(save_dir):
            os.makedirs(save_dir)

        # Generate Filename: YYYY-MM-DD_HH-MM-SS.jpg
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"{timestamp}.webp"
        filepath = os.path.join(save_dir, filename)

        # Capture and Save (Compressed WEBP)
        try:
            # Get the bounding box of the screen with the mouse on it (active display)
            bbox = self.get_active_monitor_rect()
            img = ImageGrab.grab(bbox=bbox, all_screens=True)
            if not self.should_save(img):
                # logger.info("Screenshot is the same as the last; not saving")
                return
            img.save(filepath, "WEBP", quality=60, method=6)  # WEBP over jpeg for better compression
            logger.info(f"Saved screenshot: {filepath}")
        except Exception as e:
            logger.error(f"Failed to grab screen: {e}")

    def should_save(self, current_image):
        # Resize and grayscale
        current_thumb = current_image.resize((50, 50), Image.Resampling.NEAREST).convert("L")
        
        # Handle first run
        if self.last_image_thumb is None:
            self.last_image_thumb = current_thumb
            return True

        # Calculate difference
        diff = ImageChops.difference(current_thumb, self.last_image_thumb)
        stat = ImageStat.Stat(diff)
        avg_diff = sum(stat.mean) / len(stat.mean)
        
        # Check threshold using SELF
        is_different = avg_diff > self.DIFF_THRESHOLD
        
        # Update the reference image ONLY if we found a difference
        if is_different:
            self.last_image_thumb = current_thumb
            
        return is_different

    def cleanup_old_screenshots(self):
        """Deletes files in the screenshot folder older than X days."""
        
        # Rate limiter - Only run cleanup once per hour (3600 seconds)
        if time.time() - self.last_cleanup_time < 3600:
            return

        days = self.config.get('delete_screenshots_after', 10)
        
        # If set to 0 or None, disable cleanup
        if not days or days <= 0:
            return

        save_dir = self.config.get('screenshot_folder', 'Screenshots')
        if not os.path.exists(save_dir):
            return

        # Update the timestamp so we don't run again for another hour
        self.last_cleanup_time = time.time()

        # Calculate cutoff time in seconds
        cutoff_time = time.time() - (days * 86400) # 86400 seconds in a day

        try:
            # Iterate over files in the directory
            for filename in os.listdir(save_dir):
                filepath = os.path.join(save_dir, filename)
                
                # Only process files (skip subdirectories)
                if os.path.isfile(filepath):
                    try:
                        file_mtime = os.path.getmtime(filepath)
                        if file_mtime < cutoff_time:
                            os.remove(filepath)
                            logger.info(f"Deleted old screenshot: {filename}")
                    except OSError as e:
                        logger.warning(f"Could not delete {filename}: {e}")
                        
        except Exception as e:
            logger.error(f"Cleanup failed: {e}")
    
    def get_active_monitor_rect(self):
        """Returns the (left, top, right, bottom) of the monitor containing the mouse."""
        class POINT(Structure):
            _fields_ = [("x", c_long), ("y", c_long)]

        class RECT(Structure):
            _fields_ = [("left", c_long), ("top", c_long), ("right", c_long), ("bottom", c_long)]

        class MONITORINFO(Structure):
            _fields_ = [("cbSize", c_long), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", c_long)]

        # 1. Get Mouse Position
        pt = POINT()
        windll.user32.GetCursorPos(byref(pt))

        # 2. Get Monitor Handle from Mouse Point (2 = MONITOR_DEFAULTTONEAREST)
        h_monitor = windll.user32.MonitorFromPoint(pt, 2)

        # 3. Get Monitor Info
        mi = MONITORINFO()
        mi.cbSize = sizeof(MONITORINFO)
        windll.user32.GetMonitorInfoA(h_monitor, byref(mi))

        # 4. Return Bounding Box
        return (mi.rcMonitor.left, mi.rcMonitor.top, mi.rcMonitor.right, mi.rcMonitor.bottom)