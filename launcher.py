#!/usr/bin/env python3
import tkinter as tk
from tkinter import ttk, font as tkFont # Renamed for clarity
import os
import json
import requests
import threading
import subprocess
import zipfile
import gdown  # Dependency: pip install gdown
import minecraft_launcher_lib  # Dependency: pip install minecraft-launcher-lib
from pathlib import Path
import uuid
import logging
import time
import shutil
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Optional, Dict, Any, List, Tuple, Callable

# --- Setup Logging ---
log_file = Path("launcher.log")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(threadName)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file, mode='a', encoding='utf-8'), # Append to the log file
        logging.StreamHandler() # Also log to console
    ]
)
logging.info("="*10 + " Launcher Started " + "="*10)

# --- Constants ---
CONFIG_URL = "https://gist.github.com/kuperjamper13/8f7402f86dfbc5b792dd4eda1a81c3ff/raw/launcher_config.json"
LOCAL_CONFIG_FILE = Path("launcher_config.json") # Store local settings in the launcher's directory

# --- Determine Minecraft Directory ---
def get_minecraft_directory() -> Path:
    """Determines the default Minecraft directory based on the operating system."""
    if os.name == 'nt': # Windows
        mc_dir = Path(os.getenv('APPDATA', '')) / '.minecraft'
    elif os.name == 'posix': # macOS/Linux
        mc_dir = Path.home() / 'Library/Application Support/minecraft' # macOS default
        if not mc_dir.exists(): # Check Linux default if macOS doesn't exist
            mc_dir = Path.home() / '.minecraft'
    else:
        logging.warning("Unsupported operating system! Using current directory for .minecraft.")
        mc_dir = Path.cwd() / '.minecraft' # Default to current dir if unsure

    logging.info(f"Determined Minecraft directory: {mc_dir}")
    return mc_dir

MINECRAFT_DIR = get_minecraft_directory()
MODS_DIR = MINECRAFT_DIR / 'mods' # Note: This might be overridden if instance dir is used later

# --- GUI Styling Constants ---
BG_COLOR = "#2E2E2E"
FG_COLOR = "#F0F0F0"
ENTRY_BG = "#3E3E3E"
ENTRY_FG = "#FFFFFF"
BUTTON_BG = "#4CAF50" # Green accent
BUTTON_FG = "#FFFFFF"
BUTTON_ACTIVE_BG = "#45a049" # Slightly darker green on click
FONT_FAMILY = "Segoe UI" # Or "Calibri", "Arial" - adjust as needed
FONT_SIZE_NORMAL = 11
FONT_SIZE_LARGE = 14

# --- Launcher Core Logic ---

class LauncherCore:
    """Handles the core logic of fetching config, installing, updating, and launching Minecraft."""

    def __init__(self, status_callback: Callable[[str, Optional[float], bool], None]):
        """
        Initializes the LauncherCore.

        Args:
            status_callback: A function to call for updating GUI status and progress.
                             Expected signature: callback(message: str, progress: Optional[float], is_error: bool)
        """
        self.status_callback = status_callback
        self.launcher_config: Dict[str, Any] = {}
        self.local_config: Dict[str, Any] = {"nickname": "", "installed_launcher_version": 0}
        self.minecraft_dir = MINECRAFT_DIR
        self.mods_dir = MODS_DIR # Default, might be changed if instance dir is implemented
        self._stop_event = threading.Event() # For potential future cancellation

        # Shared state for minecraft-launcher-lib callbacks (needs care with threading)
        self._lib_callback_lock = threading.Lock()
        self._lib_max_progress = 0
        self._lib_current_progress = 0
        self._lib_current_status = ""
        # State for mapping library progress to GUI progress bar segments
        self._current_task_progress_start = 0.0
        self._current_task_progress_end = 100.0
        self._current_task_base_status = ""

    def _update_status(self, message: str, progress: Optional[float] = None, is_error: bool = False, is_lib_update: bool = False):
        """
        Safely updates the status via the callback and logs the message.

        Args:
            message: The status message to display.
            progress: The absolute progress value (0-100) for the main bar.
                      If None, the progress bar is not updated.
            is_error: If True, logs the message as an error.
            is_lib_update: If True, indicates the update comes from a library callback.
        """
        log_prefix = "Lib Status: " if is_lib_update else "Status Update: "
        if is_error:
            logging.error(f"{log_prefix}{message}")
        else:
            # Avoid logging overly repetitive lib status updates unless it's an error
            if not is_lib_update or self._lib_current_status != message:
                 logging.info(f"{log_prefix}{message}")

        # Schedule the GUI update on the main thread
        # Use root.after to ensure it runs in the main Tkinter thread
        if 'root' in globals(): # Check if root window exists (for safety)
             root.after(0, self.status_callback, message, progress, is_error)
        else:
             # Fallback if root isn't available (e.g., testing without GUI)
             self.status_callback(message, progress, is_error)


    # --- minecraft-launcher-lib Callback Functions ---
    def _set_task_progress_range(self, start: float, end: float, base_status: str):
        """Sets the expected progress range (0-100) for the current library task."""
        with self._lib_callback_lock:
            self._current_task_progress_start = start
            self._current_task_progress_end = end
            self._current_task_base_status = base_status
            self._lib_max_progress = 0 # Reset max for the new task
            self._lib_current_progress = 0
            self._lib_current_status = ""
            # Update GUI immediately with the base status and start progress
            self._update_status(base_status, progress=start, is_lib_update=False)

    def _callback_set_status(self, text: str):
        """Callback for library status updates. Updates GUI status."""
        with self._lib_callback_lock:
            self._lib_current_status = text
            # Combine base status with library detail
            full_status = f"{self._current_task_base_status}: {text}"
            # Calculate current progress within the allocated range
            current_progress = self._current_task_progress_start
            if self._lib_max_progress > 0 and self._lib_current_progress > 0:
                 # Calculate percentage within the library task
                lib_percent = (self._lib_current_progress / self._lib_max_progress)
                # Map to the allocated range on the main progress bar
                current_progress = self._current_task_progress_start + lib_percent * (self._current_task_progress_end - self._current_task_progress_start)

            self._update_status(full_status, progress=current_progress, is_lib_update=True)

    def _callback_set_progress(self, value: int):
        """Callback for library progress updates. Updates GUI progress."""
        with self._lib_callback_lock:
            self._lib_current_progress = value
            current_progress = self._current_task_progress_start # Default to start if max is 0

            if self._lib_max_progress > 0:
                # Calculate percentage within the library task
                lib_percent = (value / self._lib_max_progress)
                # Map to the allocated range on the main progress bar
                current_progress = self._current_task_progress_start + lib_percent * (self._current_task_progress_end - self._current_task_progress_start)
                # Log detailed progress less frequently to avoid spamming logs
                # if value % (self._lib_max_progress // 10 if self._lib_max_progress > 10 else 1) == 0 or value == self._lib_max_progress:
                #      logging.info(f"Lib Progress: {value}/{self._lib_max_progress} ({lib_percent*100:.1f}%) -> GUI: {current_progress:.1f}%")
            else:
                # logging.info(f"Lib Progress: {value}/? -> GUI: {current_progress:.1f}%")
                pass # Avoid logging if max is 0

            # Combine base status with current library status (if any)
            status_detail = f": {self._lib_current_status}" if self._lib_current_status else ""
            full_status = f"{self._current_task_base_status}{status_detail}"
            self._update_status(full_status, progress=current_progress, is_lib_update=True)


    def _callback_set_max(self, value: int):
        """Callback for library max progress value."""
        with self._lib_callback_lock:
            # Ignore max value of 0, can happen sometimes
            if value <= 0:
                logging.warning(f"Lib Max Set ignored: {value}")
                return
            self._lib_max_progress = value
            self._lib_current_progress = 0 # Reset progress for this step
            logging.info(f"Lib Max Set: {value}")
            # Update status immediately, showing 0 progress for the new max
            status_detail = f": {self._lib_current_status}" if self._lib_current_status else ""
            full_status = f"{self._current_task_base_status}{status_detail}"
            self._update_status(full_status, progress=self._current_task_progress_start, is_lib_update=True)

    @property
    def lib_callbacks(self) -> Dict[str, Callable]:
        """ Returns the dictionary of callbacks for minecraft-launcher-lib. """
        return {
            "setStatus": self._callback_set_status,
            "setProgress": self._callback_set_progress,
            "setMax": self._callback_set_max
        }

    # --- Configuration Handling ---
    def load_local_config(self) -> Dict[str, Any]:
        """Loads nickname, installed version, gist_url, and max_ram from local file."""
        # Define defaults
        defaults = {
            "nickname": "",
            "installed_launcher_version": 0,
            "gist_url": CONFIG_URL, # Default to the hardcoded constant
            "max_ram": "4G" # Default RAM
        }
        if LOCAL_CONFIG_FILE.exists():
            logging.info(f"Attempting to load local config from {LOCAL_CONFIG_FILE}")
            try:
                with open(LOCAL_CONFIG_FILE, 'r', encoding='utf-8') as f:
                    loaded_data = json.load(f)
                if isinstance(loaded_data, dict):
                    # Merge loaded data with defaults, ensuring all keys exist
                    self.local_config = {**defaults, **loaded_data}
                    logging.info(f"Loaded local config: {self.local_config}")
                else:
                    logging.warning("Local config file has invalid format. Using defaults.")
                    self.local_config = defaults # Reset to defaults
            except json.JSONDecodeError as e:
                logging.error(f"Error decoding local config file {LOCAL_CONFIG_FILE}: {e}. Using defaults.")
                self._update_status(f"Error reading local config: {e}", is_error=True)
                self.local_config = defaults # Reset to defaults
            except Exception as e:
                logging.exception(f"Unexpected error loading local config: {e}")
                self._update_status(f"Error loading config: {e}", is_error=True)
                self.local_config = defaults # Reset to defaults
        else:
            logging.info("Local config file not found. Using defaults.")
            self.local_config = defaults # Use defaults if file doesn't exist

        # Ensure essential keys have default values if somehow missing after load
        for key, default_value in defaults.items():
            if key not in self.local_config:
                 logging.warning(f"Key '{key}' missing from loaded config, adding default: {default_value}")
                 self.local_config[key] = default_value

        return self.local_config # Return the full dictionary

    def save_local_config(self, nickname: str, gist_url: Optional[str] = None, max_ram: Optional[str] = None) -> bool:
        """Saves current settings (nickname, gist_url, max_ram, installed_version) to local file."""
        if not nickname: # Nickname is still mandatory for launch
            logging.warning("Attempted to save empty nickname. Skipping save.")
            # Don't update status here, let the caller handle UI feedback
            return False

        # Update the internal config dictionary
        self.local_config["nickname"] = nickname
        if gist_url is not None:
             self.local_config["gist_url"] = gist_url
        if max_ram is not None:
             self.local_config["max_ram"] = max_ram
        # installed_launcher_version is updated by _update_modpack

        logging.info(f"Attempting to save local config: {self.local_config}")
        try:
            # Ensure the directory exists before writing
            LOCAL_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(LOCAL_CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.local_config, f, indent=4)
            logging.info("Local config saved successfully.")
            return True
        except Exception as e:
            logging.exception(f"Error saving local config to {LOCAL_CONFIG_FILE}: {e}")
            self._update_status(f"Error saving config: {e}", is_error=True)
            return False

    def fetch_launcher_config(self) -> bool:
        """Fetches the latest config from the Gist URL specified in local config."""
        self._update_status("Fetching remote configuration...", progress=5)
        gist_url = self.local_config.get("gist_url", CONFIG_URL) # Use loaded URL, fallback to constant
        if not gist_url:
             logging.error("Gist URL is empty in local config. Cannot fetch remote config.")
             self._update_status("Error: Gist URL is not configured.", is_error=True)
             return False

        try:
            timestamp = int(time.time())
            # Ensure URL has a scheme
            if not gist_url.startswith(('http://', 'https://')):
                 gist_url = 'https://' + gist_url # Assume https if missing
                 logging.warning(f"Prepended 'https://' to Gist URL: {gist_url}")

            url_with_timestamp = f"{gist_url}?t={timestamp}" # Add timestamp to try bypassing cache
            logging.info(f"Fetching config from: {url_with_timestamp}")
            headers = {'Cache-Control': 'no-cache', 'Pragma': 'no-cache'}
            response = requests.get(url_with_timestamp, headers=headers, timeout=20)
            response.raise_for_status()

            self.launcher_config = response.json()
            logging.info(f"Fetched remote config: {self.launcher_config}")
            self._update_status("Remote configuration fetched.", progress=10)
            return True
        except requests.exceptions.Timeout:
            logging.error("Timeout occurred while fetching remote config.")
            self._update_status("Error: Timeout fetching remote configuration.", is_error=True)
            self.launcher_config = {}
            return False
        except requests.exceptions.RequestException as e:
            logging.error(f"Error fetching remote config: {e}")
            self._update_status(f"Error fetching remote config: {e}", is_error=True)
            self.launcher_config = {}
            return False
        except json.JSONDecodeError as e:
            logging.error(f"Error decoding remote config JSON: {e}")
            self._update_status("Error: Invalid format in remote configuration file.", is_error=True)
            self.launcher_config = {}
            return False
        except Exception as e:
            logging.exception("An unexpected error occurred during config fetch.")
            self._update_status(f"An unexpected error occurred: {e}", is_error=True)
            self.launcher_config = {}
            return False

    # --- Installation/Update/Launch Steps ---
    def _ensure_directories(self) -> bool:
        """Ensures Minecraft and Mods directories exist."""
        try:
            self._update_status("Checking Minecraft directory...", progress=12) # Keep progress low
            self.minecraft_dir.mkdir(parents=True, exist_ok=True)
            self.mods_dir.mkdir(parents=True, exist_ok=True) # Ensure default mods dir exists too
            logging.info(f"Ensured Minecraft directory exists: {self.minecraft_dir}")
            logging.info(f"Ensured Mods directory exists: {self.mods_dir}")
            return True
        except OSError as e:
            logging.exception(f"Error creating directories: {e}")
            self._update_status(f"Error creating directories: {e}", is_error=True)
            return False

    def _install_minecraft_version(self, mc_version: str, progress_start: float, progress_end: float, max_retries: int = 3, retry_delay: int = 5) -> bool:
        """
        Installs the specified vanilla Minecraft version with retries and detailed progress.

        Args:
            mc_version: The Minecraft version string (e.g., "1.16.5").
            progress_start: The starting percentage for this task on the main progress bar.
            progress_end: The ending percentage for this task on the main progress bar.
            max_retries: Maximum number of installation attempts.
            retry_delay: Seconds to wait between retries.

        Returns:
            True if installation succeeded or version already exists, False otherwise.
        """
        task_name = f"Minecraft {mc_version}"
        base_status = f"Installing {task_name}"
        logging.info(f"Starting task: {base_status}")
        self._set_task_progress_range(progress_start, progress_end, base_status) # Setup progress mapping

        last_exception = None
        for attempt in range(1, max_retries + 1):
            logging.info(f"Attempt {attempt}/{max_retries} to install {task_name}...")
            if attempt > 1:
                # Update status for retry, keeping progress at the start of the range
                self._update_status(f"Retrying {base_status} (Attempt {attempt}/{max_retries})...", progress=progress_start)
                time.sleep(retry_delay)
            else:
                 # Initial status update for the first attempt
                 self._update_status(f"{base_status} (Attempt 1/{max_retries})...", progress=progress_start)


            try:
                # --- Call library install directly in this thread ---
                logging.info(f"Calling minecraft_launcher_lib.install.install_minecraft_version for {mc_version} (Attempt {attempt})")
                minecraft_launcher_lib.install.install_minecraft_version(
                    mc_version,
                    str(self.minecraft_dir),
                    callback=self.lib_callbacks
                )
                logging.info(f"Finished install call for {task_name} on attempt {attempt}.")
                # Final status update for success, setting progress to the end of the range
                self._update_status(f"{task_name} installation complete.", progress=progress_end)
                logging.info(f"Task finished successfully: Install {task_name}")
                return True # Success, exit the retry loop

            except Exception as e: # Catch exceptions from the library call
                 last_exception = e
                 logging.warning(f"Attempt {attempt} failed for installing {task_name}: {e}")
                 # Update status with error, keeping progress at the start
                 self._update_status(f"Attempt {attempt} failed for {base_status}: {e}", progress=progress_start, is_error=True)
                 # Fall through to retry or final error reporting

        # --- All attempts failed ---
        logging.error(f"All {max_retries} attempts to install {task_name} failed.")
        error_msg = f"Failed to install {task_name}"
        if last_exception:
            logging.exception(f"Last error during install attempt for {task_name}: {last_exception}")
            # Try to provide a slightly more specific error message
            if "HTTPSConnectionPool" in str(last_exception):
                 error_msg += ": Network error (check connection?)"
            elif "checksum" in str(last_exception).lower():
                 error_msg += ": File download error (checksum mismatch)."
            else:
                 error_msg += f": {last_exception}"
        # Final error status update, keeping progress at the start of the range
        self._update_status(error_msg, progress=progress_start, is_error=True)

        # Check if it exists anyway (maybe installed previously or partially)
        logging.info(f"Checking if {task_name} exists despite installation errors...")
        try:
            installed_versions = minecraft_launcher_lib.utils.get_installed_versions(str(self.minecraft_dir))
            if any(v['id'] == mc_version for v in installed_versions):
                logging.warning(f"Installation failed, but found existing {task_name}. Attempting to continue.")
                self._update_status(f"Using existing {task_name}.", progress=progress_end) # Set progress to end if using existing
                return True # Allow continuing
        except Exception as check_e:
            logging.error(f"Could not check for existing versions after install errors: {check_e}")

        logging.error(f"Task failed: Install {task_name}")
        return False # Definite failure

    def _install_forge(self, mc_version: str, loader_version: str, progress_start: float, progress_end: float) -> Optional[str]:
        """
        Installs Forge using the official installer with progress updates.

        Args:
            mc_version: Minecraft version.
            loader_version: Forge version.
            progress_start: Starting percentage for this task.
            progress_end: Ending percentage for this task.

        Returns:
            The Forge version ID string if successful, None otherwise.
        """
        version_id = f"{mc_version}-forge-{loader_version}"
        task_name = f"Forge {loader_version}"
        base_status = f"Installing {task_name}"
        logging.info(f"Starting task: {base_status}")
        self._update_status(f"{base_status}...", progress=progress_start) # Initial status

        installer_filename = f"forge-{mc_version}-{loader_version}-installer.jar"
        installer_path = self.minecraft_dir / installer_filename
        installer_url = f"https://maven.minecraftforge.net/net/minecraftforge/forge/{mc_version}-{loader_version}/{installer_filename}"

        # Define progress sub-ranges within the allocated range
        check_start, check_end = progress_start, progress_start + (progress_end - progress_start) * 0.05 # 5% for check
        dl_start, dl_end = check_end, check_end + (progress_end - check_end) * 0.6 # 60% of remaining for download
        install_start, install_end = dl_end, dl_end + (progress_end - dl_end) * 0.8 # 80% of remaining for install run
        verify_start, verify_end = install_end, progress_end # Rest for verify

        # --- Pre-flight Checks ---
        java_path = shutil.which('java')
        if not java_path:
            logging.error("Forge install check failed: 'java' command not found.")
            self._update_status("Error: Java not found. Please install Java and ensure it's in your PATH.", progress=progress_start, is_error=True)
            return None
        logging.info(f"Java executable found at: {java_path}")

        self._update_status(f"Checking {task_name} installer availability...", progress=check_start)
        logging.info(f"Checking Forge installer URL (HEAD): {installer_url}")
        try:
            response = requests.head(installer_url, timeout=15) # Short timeout for HEAD
            response.raise_for_status() # Check for 4xx/5xx errors
            logging.info(f"Forge installer URL check successful (Status: {response.status_code}).")
            self._update_status(f"Checking {task_name} installer availability... OK", progress=check_end)
        except requests.exceptions.Timeout:
            logging.error(f"Forge installer URL check timed out: {installer_url}")
            self._update_status(f"Error checking {task_name} availability (Timeout)", progress=check_start, is_error=True)
            return None
        except requests.exceptions.RequestException as e:
            logging.error(f"Forge installer URL check failed: {e}")
            error_msg = f"Error checking {task_name} availability"
            if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
                 if e.response.status_code == 404:
                     error_msg = f"Error: {task_name} installer not found for {mc_version}"
                 else:
                     error_msg += f" (HTTP {e.response.status_code})"
            else:
                 error_msg += f": {e}"
            self._update_status(error_msg, progress=check_start, is_error=True)
            return None

        # --- Installation Process ---
        download_success = False
        download_attempts = 3
        last_download_exception = None

        for attempt in range(1, download_attempts + 1):
            if attempt > 1:
                logging.warning(f"Retrying {task_name} installer download (Attempt {attempt}/{download_attempts})...")
                self._update_status(f"Retrying {task_name} download (Attempt {attempt})...", progress=dl_start)
                time.sleep(5)

            try:
                self._update_status(f"Downloading {task_name} installer (Attempt {attempt})...", progress=dl_start)
                logging.info(f"Attempt {attempt}: Downloading {task_name} installer from {installer_url} to {installer_path}")
                response = requests.get(installer_url, stream=True, timeout=300) # Longer timeout for download
                response.raise_for_status()
                total_size = int(response.headers.get('content-length', 0)) # Can be 0 if server doesn't provide it
                bytes_downloaded = 0
                last_progress_update_time = time.monotonic()
                with open(installer_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk: # filter out keep-alive new chunks
                            f.write(chunk)
                            bytes_downloaded += len(chunk)
                            # Update progress bar during download, mapping to dl_start -> dl_end range
                            current_time = time.monotonic()
                            if total_size > 0:
                                dl_percent = bytes_downloaded / total_size
                                current_gui_progress = dl_start + dl_percent * (dl_end - dl_start)
                                # Throttle GUI updates slightly
                                if current_time - last_progress_update_time > 0.1 or bytes_downloaded == total_size:
                                    self._update_status(f"Downloading {task_name}... {bytes_downloaded/1024/1024:.1f}/{total_size/1024/1024:.1f} MB", progress=current_gui_progress)
                                    last_progress_update_time = current_time
                            elif current_time - last_progress_update_time > 0.5: # Update less often if no total size
                                current_gui_progress = dl_start + (dl_end - dl_start) * 0.5 # Show indeterminate 50% within range
                                self._update_status(f"Downloading {task_name}... {bytes_downloaded/1024/1024:.1f} MB", progress=current_gui_progress)
                                last_progress_update_time = current_time
                        # else: # Removed logging for empty chunks, too noisy
                        #     pass

                # Verify download size if total_size was provided
                if total_size > 0 and bytes_downloaded < total_size:
                    raise requests.exceptions.RequestException(f"Incomplete download: Expected {total_size} bytes, got {bytes_downloaded}")

                logging.info(f"{task_name} installer downloaded successfully on attempt {attempt} ({bytes_downloaded} bytes).")
                self._update_status(f"{task_name} installer downloaded.", progress=dl_end) # Mark download complete
                download_success = True
                break # Exit download retry loop

            except requests.exceptions.RequestException as e:
                last_download_exception = e
                logging.error(f"Attempt {attempt} failed to download {task_name} installer: {e}")
                if installer_path.exists():
                    try: installer_path.unlink() # Clean up partial download
                    except OSError: pass
                # Keep progress at dl_start on error
                self._update_status(f"Error downloading {task_name} (Attempt {attempt}): {e}", progress=dl_start, is_error=True)

        if not download_success:
            logging.error(f"Failed to download {task_name} installer after {download_attempts} attempts.")
            self._update_status(f"Error downloading {task_name} installer: {last_download_exception}", progress=dl_start, is_error=True)
            return None

        # --- Run Installer ---
        try:
            self._update_status(f"Running {task_name} installer...", progress=install_start) # Indicate installer start
            command = [java_path, "-jar", str(installer_path), "--installClient"]
            logging.info(f"Running Forge installer command: {' '.join(command)}")
            # Use Popen for potentially long-running process, capture output later if needed
            process = subprocess.Popen(
                command, cwd=str(self.minecraft_dir),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', errors='replace'
            )

            # Optional: Could add a timeout here if the installer hangs indefinitely
            stdout, stderr = process.communicate(timeout=300) # 5 minute timeout for installer run

            if stdout: logging.info(f"Forge Installer STDOUT:\n{stdout.strip()}")
            if stderr:
                log_level = logging.ERROR if process.returncode != 0 else logging.WARNING
                logging.log(log_level, f"Forge Installer STDERR:\n{stderr.strip()}")

            if process.returncode != 0:
                logging.error(f"Forge installer failed with return code {process.returncode}.")
                error_message = f"Forge installer failed (code {process.returncode})"
                # Try to parse common errors from stderr for better user feedback
                if "java.net" in stderr: error_message += ": Network error during install."
                elif "FileNotFoundException" in stderr: error_message += ": File not found during install."
                elif "Could not find main class" in stderr: error_message += ": Corrupted download or Java issue."
                elif "Target directory" in stderr and "invalid" in stderr: error_message += ": Invalid target directory?"
                else: error_message += ". Check log."
                self._update_status(error_message, progress=install_start, is_error=True)
                return None

            logging.info(f"Forge installer process completed successfully (RC: {process.returncode}).")
            self._update_status(f"{task_name} installer finished.", progress=install_end) # Installer done

            # --- Verify Installation ---
            logging.info(f"Verifying {task_name} installation: {version_id}")
            self._update_status(f"Verifying {task_name} installation...", progress=verify_start) # Verification step
            installed_versions = minecraft_launcher_lib.utils.get_installed_versions(str(self.minecraft_dir))
            if any(v['id'] == version_id for v in installed_versions):
                logging.info(f"{task_name} version {version_id} successfully verified.")
                self._update_status(f"{task_name} installed successfully.", progress=verify_end) # Final success for Forge
                logging.info(f"Task finished successfully: Install {task_name}")
                return version_id # Success!
            else:
                logging.error(f"Forge installer ran, but version ID '{version_id}' not found.")
                self._update_status(f"Warning: {task_name} install verification failed.", progress=verify_start, is_error=True)
                return None

        except subprocess.TimeoutExpired:
            logging.error(f"Forge installer timed out after 300 seconds.")
            self._update_status(f"Error: {task_name} installer timed out.", progress=install_start, is_error=True)
            try:
                 if process.poll() is None: # Check if process is still running
                      process.kill() # Ensure the process is terminated if possible
                      process.wait() # Wait for termination
            except Exception as kill_e:
                 logging.warning(f"Could not kill timed-out Forge process: {kill_e}")
            return None
        except FileNotFoundError: # If java_path becomes invalid between check and run
            logging.error("Forge installer run failed: 'java' command not found.")
            self._update_status("Error: Java not found. Please install Java and ensure it's in your PATH.", progress=install_start, is_error=True)
            return None
        except Exception as e:
            logging.exception(f"An unexpected error occurred during {task_name} installation: {e}")
            self._update_status(f"Error installing {task_name}: {e}", progress=install_start, is_error=True)
            return None
        finally:
            # Ensure installer JAR is cleaned up
            if installer_path.exists():
                try:
                    installer_path.unlink()
                    logging.info(f"{task_name} installer file '{installer_path.name}' cleaned up.")
                except OSError as e:
                    logging.warning(f"Could not delete {task_name} installer {installer_path}: {e}")

    def _install_fabric(self, mc_version: str, loader_version: str, progress_start: float, progress_end: float, max_retries: int = 3, retry_delay: int = 5) -> Optional[str]:
        """
        Installs Fabric using minecraft-launcher-lib with retries and progress.

        Args:
            mc_version: Minecraft version.
            loader_version: Fabric loader version.
            progress_start: Starting percentage for this task.
            progress_end: Ending percentage for this task.
            max_retries: Max installation attempts.
            retry_delay: Delay between retries.

        Returns:
            Fabric version ID string if successful, None otherwise.
        """
        task_name = f"Fabric {loader_version}"
        base_status = f"Installing {task_name}"
        logging.info(f"Starting task: {base_status}")
        self._set_task_progress_range(progress_start, progress_end, base_status) # Setup progress mapping
        last_exception = None

        for attempt in range(1, max_retries + 1):
            logging.info(f"Attempt {attempt}/{max_retries} to install {task_name} for {mc_version}...")
            if attempt > 1:
                self._update_status(f"Retrying {base_status} (Attempt {attempt}/{max_retries})...", progress=progress_start)
                time.sleep(retry_delay)
            else:
                self._update_status(f"{base_status} (Attempt 1/{max_retries})...", progress=progress_start)

            try:
                # Reset lib progress state (handled by _set_task_progress_range)

                logging.info(f"Calling minecraft_launcher_lib.fabric.install_fabric for {mc_version}, {loader_version} (Attempt {attempt})")
                minecraft_launcher_lib.fabric.install_fabric(
                    mc_version, loader_version, str(self.minecraft_dir), callback=self.lib_callbacks
                )
                logging.info(f"Call to install_fabric for {task_name} completed on attempt {attempt}.")
                self._update_status(f"{task_name} installation complete.", progress=progress_end) # Final success status

                # Verify and find version ID
                installed_versions = minecraft_launcher_lib.utils.get_installed_versions(str(self.minecraft_dir))
                for v in installed_versions:
                    # Make matching slightly more robust
                    if v['type'] == 'release' and mc_version in v['id'] and 'fabric-loader' in v['id'] and loader_version in v['id']:
                        logging.info(f"Detected Fabric version ID: {v['id']}")
                        logging.info(f"Task finished successfully: Install {task_name}")
                        return v['id'] # Return detected ID

                # If exact match not found, construct a likely ID and warn
                fallback_id = f"fabric-loader-{loader_version}-{mc_version}"
                logging.warning(f"Could not auto-detect exact Fabric version ID after install. Using fallback: {fallback_id}")
                # Check if the fallback ID actually exists
                if any(v['id'] == fallback_id for v in installed_versions):
                    logging.info(f"Fallback ID {fallback_id} confirmed in installed versions.")
                    logging.info(f"Task finished successfully (using fallback ID): Install {task_name}")
                    return fallback_id
                else:
                    # If even the fallback isn't found, something is wrong
                    logging.error(f"Fabric install seemed to succeed, but neither auto-detected nor fallback ID ({fallback_id}) found.")
                    last_exception = RuntimeError("Fabric install verification failed.") # Create an exception for reporting
                    # Fall through to error handling below

            except Exception as e:
                last_exception = e
                logging.warning(f"Attempt {attempt} failed for installing {task_name}: {e}")
                self._update_status(f"Attempt {attempt} failed for {base_status}: {e}", progress=progress_start, is_error=True)

        # --- All attempts failed ---
        logging.error(f"All {max_retries} attempts to install {task_name} failed.")
        error_msg = f"Failed to install {task_name}"
        if last_exception:
            logging.exception(f"Last error during {task_name} install attempt: {last_exception}")
            error_msg += f": {last_exception}"
        self._update_status(error_msg, progress=progress_start, is_error=True)
        logging.error(f"Task failed: Install {task_name}")
        return None

    def _update_modpack(self, mods_url: Optional[str], gist_launcher_version: int, progress_start: float, progress_end: float) -> bool:
        """
        Handles clearing old mods and downloading/extracting the new modpack with progress.

        Args:
            mods_url: URL of the modpack zip (direct or GDrive).
            gist_launcher_version: Version number from the remote config.
            progress_start: Starting percentage for this task.
            progress_end: Ending percentage for this task.

        Returns:
            True if successful or no update needed, False on error.
        """
        task_name = "Modpack Update"
        base_status = "Updating Modpack"
        logging.info(f"Starting task: {task_name}")
        installed_launcher_version = self.local_config.get("installed_launcher_version", 0)
        logging.info(f"Checking modpack update: Gist Version={gist_launcher_version}, Local Version={installed_launcher_version}")
        needs_mod_update = gist_launcher_version > installed_launcher_version
        modpack_configured = bool(mods_url)

        # Define progress sub-ranges
        clear_start, clear_end = progress_start, progress_start + (progress_end - progress_start) * 0.1 # 10% for clear
        dl_start, dl_end = clear_end, clear_end + (progress_end - clear_end) * 0.6 # 60% of remaining for download
        extract_start, extract_end = dl_end, dl_end + (progress_end - dl_end) * 0.8 # 80% of remaining for extract
        structure_start, structure_end = extract_end, progress_end # Rest for structure check

        if not modpack_configured:
            self._update_status("No modpack configured.", progress=progress_end) # Jump to end
            if self.mods_dir.exists() and any(self.mods_dir.iterdir()):
                self._update_status("No modpack configured. Clearing local mods folder...", progress=clear_start)
                if self._clear_mods_folder(clear_start, clear_end): # Pass progress range
                    self._update_status("Local mods folder cleared.", progress=progress_end) # Jump to end after clear
                else:
                    logging.error(f"Task failed: {task_name} (clearing failed)")
                    # Error status already set by _clear_mods_folder
                    return False # Stop if clearing failed
            logging.info(f"Task finished successfully: {task_name} (no update needed/done)")
            return True

        if not needs_mod_update:
            logging.info("Modpack is up-to-date. No update needed.")
            self._update_status("Modpack is up-to-date.", progress=progress_end) # Jump to end
            logging.info(f"Task finished successfully: {task_name} (up-to-date)")
            return True

        # --- Mod Update Required ---
        logging.info(f"Newer modpack version ({gist_launcher_version}) found. Starting update process.")
        self._update_status(f"New version ({gist_launcher_version}) found. Updating modpack...", progress=progress_start)

        # 1. Clear existing mods
        logging.info("Attempting to clear mods folder...")
        if not self._clear_mods_folder(clear_start, clear_end): # Pass progress range
            logging.error(f"Task failed: {task_name} (clearing failed)")
            # Error status already set by _clear_mods_folder
            return False

        # 2. Download new mods
        self._update_status(f"Downloading modpack...", progress=dl_start)
        download_path = Path("mods_temp.zip")
        try:
            is_direct_zip = mods_url.lower().startswith(('http://', 'https://')) and mods_url.lower().endswith('.zip')

            if is_direct_zip:
                logging.info(f"Downloading modpack from direct URL: {mods_url}")
                response = requests.get(mods_url, stream=True, timeout=300) # Increased timeout
                response.raise_for_status()
                total_size = int(response.headers.get('content-length', 0))
                bytes_downloaded = 0
                last_progress_update_time = time.monotonic()
                with open(download_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            bytes_downloaded += len(chunk)
                            current_time = time.monotonic()
                            if total_size > 0:
                                dl_percent = bytes_downloaded / total_size
                                current_gui_progress = dl_start + dl_percent * (dl_end - dl_start)
                                # Throttle GUI updates
                                if current_time - last_progress_update_time > 0.1 or bytes_downloaded == total_size:
                                    self._update_status(f"Downloading modpack... {bytes_downloaded/1024/1024:.1f}/{total_size/1024/1024:.1f} MB", progress=current_gui_progress)
                                    last_progress_update_time = current_time
                            elif current_time - last_progress_update_time > 0.5: # Update less often if no total size
                                current_gui_progress = dl_start + (dl_end - dl_start) * 0.5 # Indeterminate
                                self._update_status(f"Downloading modpack... {bytes_downloaded/1024/1024:.1f} MB", progress=current_gui_progress)
                                last_progress_update_time = current_time

                if total_size > 0 and bytes_downloaded < total_size:
                     raise requests.exceptions.RequestException(f"Incomplete download: Expected {total_size} bytes, got {bytes_downloaded}")

                logging.info(f"Modpack downloaded successfully ({bytes_downloaded} bytes).")
                self._update_status("Modpack downloaded. Extracting...", progress=dl_end) # Mark download complete
            else:
                # gdown doesn't offer easy progress hooks
                logging.info(f"Downloading modpack from Google Drive URL: {mods_url}")
                self._update_status("Downloading modpack (Google Drive)...", progress=dl_start + (dl_end - dl_start) * 0.5) # Show indeterminate progress
                gdown.download(mods_url, str(download_path), quiet=False, fuzzy=True) # Consider adding timeout if gdown supports it
                logging.info(f"Modpack downloaded via gdown to {download_path}")
                self._update_status("Modpack downloaded. Extracting...", progress=dl_end) # Mark download complete

            # 3. Extract mods
            logging.info(f"Attempting to extract {download_path} to {self.mods_dir}")
            try:
                with zipfile.ZipFile(download_path, 'r') as zip_ref:
                    zip_contents = zip_ref.namelist()
                    logging.info(f"Zip file contents: {zip_contents}")
                    # Show status before and after extraction
                    self._update_status("Extracting modpack...", progress=extract_start) # Start extraction phase
                    zip_ref.extractall(self.mods_dir)
                logging.info(f"Successfully extracted zip to {self.mods_dir}")
                self._update_status("Modpack extracted.", progress=extract_end) # Extraction done
                mods_dir_contents = os.listdir(self.mods_dir)
                logging.info(f"Mods directory contents after extraction: {mods_dir_contents}")

                # Check for nested directory structure (quick step)
                self._update_status("Checking modpack structure...", progress=structure_start)
                self._adjust_nested_mod_directory()
                self._update_status("Modpack structure checked.", progress=structure_end)

            except zipfile.BadZipFile:
                logging.error(f"Error extracting modpack: '{download_path}' is not a valid zip file.")
                self._update_status("Error: Downloaded modpack file is corrupted or not a zip.", progress=extract_start, is_error=True)
                return False
            except Exception as extract_e:
                logging.exception(f"An unexpected error occurred during modpack extraction: {extract_e}")
                self._update_status(f"Error extracting mods: {extract_e}", progress=extract_start, is_error=True)
                return False

            # 4. Update local config version *after* successful extraction
            self.local_config["installed_launcher_version"] = gist_launcher_version
            # Save happens later in the main sequence

            self._update_status("Modpack update process complete.", progress=progress_end) # Final step for modpack update phase
            logging.info(f"Task finished successfully: {task_name}")
            return True

        except requests.exceptions.RequestException as e:
            logging.exception(f"Error downloading modpack via requests: {e}")
            self._update_status(f"Error downloading modpack: {e}", progress=dl_start, is_error=True)
            return False
        except gdown.exceptions.GDownException as e:
            logging.error(f"gdown error downloading modpack: {e}")
            self._update_status(f"Error downloading modpack (check GDrive URL/permissions?): {e}", progress=dl_start, is_error=True)
            return False
        except Exception as e:
            logging.exception(f"An unexpected error occurred during modpack update: {e}")
            self._update_status(f"Error updating mods: {e}", progress=progress_start, is_error=True) # Use overall start progress
            return False
        finally:
            if download_path.exists():
                try:
                    download_path.unlink()
                    logging.info("Temporary modpack zip file deleted.")
                except OSError as e:
                    logging.warning(f"Could not delete temporary modpack file {download_path}: {e}")

    def _clear_mods_folder(self, progress_start: float, progress_end: float) -> bool:
        """Clears the contents of the mods folder with progress indication."""
        if not self.mods_dir.exists():
            logging.info("Mods directory does not exist, nothing to clear.")
            self._update_status("Mods directory clear (already empty).", progress=progress_end)
            return True

        self._update_status("Deleting old mods...", progress=progress_start)
        logging.info(f"Clearing mods folder: {self.mods_dir}")
        items_deleted = 0
        items_failed = 0
        try:
            all_items = list(self.mods_dir.iterdir())
            total_items = len(all_items)
            for i, item in enumerate(all_items):
                try:
                    if item.is_file() or item.is_symlink():
                        item.unlink()
                        items_deleted += 1
                    elif item.is_dir():
                        shutil.rmtree(item)
                        items_deleted += 1
                    logging.debug(f"Deleted: {item.name}")

                    # Update progress during deletion
                    if total_items > 0:
                        clear_percent = (i + 1) / total_items
                        current_gui_progress = progress_start + clear_percent * (progress_end - progress_start)
                        # Update less frequently for speed
                        if i % 5 == 0 or i == total_items - 1:
                             self._update_status(f"Deleting old mods... ({i+1}/{total_items})", progress=current_gui_progress)

                except Exception as e:
                    items_failed += 1
                    logging.error(f"Failed to delete {item}: {e}")

            if items_failed > 0:
                logging.error(f"Failed to delete {items_failed} items in mods folder.")
                self._update_status(f"Error: Could not delete all old mods (failed: {items_failed}). Check permissions.", progress=progress_start, is_error=True)
                return False
            else:
                logging.info(f"Successfully deleted {items_deleted} items from mods folder.")
                self._update_status("Old mods deleted.", progress=progress_end)
                return True
        except Exception as e:
            logging.exception(f"Error listing items in mods folder for clearing: {e}")
            self._update_status(f"Error clearing mods folder: {e}", progress=progress_start, is_error=True)
            return False

    def _adjust_nested_mod_directory(self):
        """Checks for and corrects a common issue where mods are extracted into a single subfolder."""
        try:
            mods_dir_items = list(self.mods_dir.iterdir())
            if len(mods_dir_items) == 1 and mods_dir_items[0].is_dir():
                nested_dir = mods_dir_items[0]
                logging.warning(f"Detected single nested directory: {nested_dir.name}. Moving contents up.")
                self._update_status("Adjusting nested mod directory structure...")
                moved_count = 0
                for item in nested_dir.iterdir():
                    try:
                        target_path = self.mods_dir / item.name
                        shutil.move(str(item), str(target_path))
                        moved_count += 1
                    except Exception as move_e:
                        logging.error(f"Failed to move item {item.name} from nested directory: {move_e}")
                logging.info(f"Moved {moved_count} items from {nested_dir.name} to {self.mods_dir}.")
                try:
                    nested_dir.rmdir()
                    logging.info(f"Removed empty nested directory: {nested_dir.name}")
                except OSError as rmdir_e:
                    logging.warning(f"Could not remove nested directory {nested_dir.name}: {rmdir_e}")
            else:
                logging.info("Mods directory structure seems correct.")
        except Exception as structure_check_e:
            logging.exception(f"Error checking/adjusting mod directory structure: {structure_check_e}")

    def _launch_minecraft(self, version_id: str, nickname: str) -> bool:
        """Launches Minecraft using the specified version ID and nickname."""
        task_name = "Launch Minecraft"
        logging.info(f"Starting task: {task_name}")
        self._update_status(f"Preparing to launch Minecraft {version_id}...", progress=96)
        logging.info(f"Preparing launch for version='{version_id}', nickname='{nickname}'")

        # Start with JVM args from remote config, if any
        jvm_args = self.launcher_config.get("jvm_args", [])
        if not isinstance(jvm_args, list): # Ensure it's a list
             logging.warning(f"Invalid jvm_args format in remote config ({type(jvm_args)}), using empty list.")
             jvm_args = []

        # Get Max RAM setting from local config
        max_ram_setting = self.local_config.get("max_ram", "4G").strip().upper()
        if re.match(r"^\d+[GM]$", max_ram_setting):
             # Remove any existing -Xmx argument
             jvm_args = [arg for arg in jvm_args if not arg.startswith("-Xmx")]
             # Add the new -Xmx argument from settings
             jvm_args.append(f"-Xmx{max_ram_setting}")
             logging.info(f"Applied Max RAM setting: -Xmx{max_ram_setting}")
        else:
             logging.warning(f"Invalid max_ram setting '{max_ram_setting}' in local config. Using default JVM args.")


        options = {
            "username": nickname,
            "uuid": str(uuid.uuid3(uuid.NAMESPACE_DNS, nickname)), # Offline mode UUID
            "token": "0", # Offline mode token
            "jvmArguments": jvm_args
            # Add "gameDirectory": str(INSTANCE_DIR) here if implementing instance dirs
        }
        logging.info(f"Using launch options: {options}")

        try:
            minecraft_command = minecraft_launcher_lib.command.get_minecraft_command(version_id, str(self.minecraft_dir), options)
            logging.info(f"Generated Minecraft command: {' '.join(minecraft_command)}")
        except Exception as e:
            logging.exception(f"Error creating launch command for {version_id}: {e}")
            self._update_status(f"Error preparing launch command: {e}", is_error=True)
            logging.error(f"Task failed: {task_name} (command generation)")
            return False

        self._update_status(f"Launching Minecraft as {nickname}...", progress=98)
        try:
            # Use Popen for non-blocking launch
            subprocess.Popen(minecraft_command, cwd=str(self.minecraft_dir)) # Run in mc dir context
            logging.info("Minecraft process started.")
            self._update_status("Minecraft launched! You can close this launcher.", progress=100)
            logging.info(f"Task finished successfully: {task_name}")
            return True
        except FileNotFoundError:
            logging.error("Launch failed: 'java' command not found. Is Java installed and in PATH?")
            self._update_status("Error: Java not found. Please install Java and ensure it's in your PATH.", is_error=True)
            logging.error(f"Task failed: {task_name} (Java not found)")
            return False
        except Exception as e:
            logging.exception(f"An unexpected error occurred during Minecraft launch: {e}")
            self._update_status(f"Error launching Minecraft: {e}", is_error=True)
            logging.error(f"Task failed: {task_name} (launch exception)")
            return False

    # --- Main Execution Method ---
    def run_tasks(self, nickname: str):
        """
        Orchestrates the entire install/update/launch process.

        Args:
            nickname: The player nickname to use.
        """
        logging.info("="*20 + " Starting Action Sequence " + "="*20)
        overall_success = False
        try:
            # --- Define Progress Ranges (Total 100%) ---
            # Initial steps: 0-12%
            fetch_config_start, fetch_config_end = 0.0, 10.0
            ensure_dirs_start, ensure_dirs_end = 10.0, 12.0
            # Installation steps: 12-95% (83% total)
            vanilla_install_start, vanilla_install_end = 12.0, 35.0 # 23%
            loader_install_start, loader_install_end = 35.0, 60.0 # 25%
            modpack_update_start, modpack_update_end = 60.0, 95.0 # 35%
            # Final steps: 95-100%
            save_config_start, save_config_end = 95.0, 96.0 # 1%
            launch_start, launch_end = 96.0, 100.0 # 4%

            # 1. Validate and Save Nickname (Initial Step - No progress bar update needed here)
            if not self.save_local_config(nickname):
                self._update_status("Error: Nickname cannot be empty or save failed.", is_error=True)
                logging.error("Action aborted: Failed to save local config.")
                return # Exit function early
            logging.info(f"Using nickname: {nickname}")

            # 2. Fetch Remote Config
            # Progress handled internally by fetch_launcher_config (sets 5% -> 10%)
            if not self.fetch_launcher_config():
                logging.error("Action aborted: Failed to fetch remote config.")
                return

            # 3. Ensure Directories Exist
            # Progress handled internally by _ensure_directories (sets 12%)
            if not self._ensure_directories():
                logging.error("Action aborted: Failed to ensure directories.")
                return

            # 4. Get Config Details
            mc_version = self.launcher_config.get("mc_version")
            raw_loader_type = self.launcher_config.get("loader_type")
            loader_type = str(raw_loader_type).lower() if raw_loader_type is not None else ""
            loader_version = self.launcher_config.get("loader_version")
            mods_url = self.launcher_config.get("mods_url")
            gist_launcher_version = self.launcher_config.get("launcher_version", 0)
            version_name = self.launcher_config.get("version_name", f"{mc_version} ({loader_type or 'Vanilla'})")

            logging.info(f"Configuration: MC={mc_version}, Loader={loader_type} {loader_version}, Modpack Version={gist_launcher_version}, Name={version_name}")

            if not mc_version:
                self._update_status("Error: 'mc_version' missing in remote config.", is_error=True)
                logging.error("Action aborted: 'mc_version' missing.")
                return

            self._update_status(f"Preparing: {version_name}", progress=ensure_dirs_end) # Show status after dirs check

            # 5. Install Vanilla Minecraft
            if not self._install_minecraft_version(mc_version, vanilla_install_start, vanilla_install_end):
                logging.error(f"Action aborted: Failed to install/verify Minecraft {mc_version}.")
                return # Exit function early

            # 6. Install Loader (Forge/Fabric)
            version_id_to_launch = mc_version # Default to vanilla
            if loader_type == "forge" and loader_version:
                version_id_to_launch = self._install_forge(mc_version, loader_version, loader_install_start, loader_install_end)
                if not version_id_to_launch:
                    logging.error(f"Action aborted: Failed to install Forge {loader_version}.")
                    return # Exit function early
            elif loader_type == "fabric" and loader_version:
                version_id_to_launch = self._install_fabric(mc_version, loader_version, loader_install_start, loader_install_end)
                if not version_id_to_launch:
                    logging.error(f"Action aborted: Failed to install Fabric {loader_version}.")
                    return # Exit function early
            else:
                self._update_status("No Mod Loader needed.", progress=loader_install_end) # Skip loader progress range
                logging.info("No loader specified or required. Using Vanilla.")

            # 7. Check/Update Modpack
            if not self._update_modpack(mods_url, gist_launcher_version, modpack_update_start, modpack_update_end):
                logging.error("Action aborted: Failed to update modpack.")
                return # Exit function early

            # 8. Save Config (with potentially updated version number from modpack)
            # Do this *after* all install/update steps succeed but *before* launch
            self._update_status("Saving configuration...", progress=save_config_start)
            if not self.save_local_config(nickname):
                 # Log warning, but don't necessarily abort launch
                 logging.warning("Failed to save updated local config before launch.")
                 self._update_status("Warning: Failed to save config.", progress=save_config_start, is_error=True)
            self._update_status("Configuration saved.", progress=save_config_end)

            # 9. Launch Game
            # Progress handled internally by _launch_minecraft (96% -> 100%)
            if not self._launch_minecraft(version_id_to_launch, nickname):
                logging.error("Action aborted: Failed to launch Minecraft.")
                # Button will be re-enabled by finally block
            else:
                logging.info("Launch sequence completed successfully.")
                overall_success = True # Mark success for finally block

        except Exception as e:
            logging.exception("An unexpected error occurred during the main action sequence.")
            self._update_status(f"An unexpected error occurred: {e}", is_error=True)

        finally:
            logging.info("="*20 + " Action Sequence Finished " + "="*20)
            # Return success status to potentially re-enable button in GUI
            return overall_success


# --- GUI Application ---

class LauncherApp:
    """The main Tkinter GUI application."""

    def __init__(self, root_window):
        self.root = root_window
        self.core = LauncherCore(self.update_status_display) # Pass GUI update method

        # GUI State
        self.settings_frame_visible = False

        self._setup_styles()
        self._setup_gui()
        self._load_initial_config() # This will now also load Gist URL/RAM for the settings panel

    def _setup_styles(self):
        """Configures styles for GUI elements."""
        self.style = ttk.Style()
        # Configure TProgressbar style
        self.style.theme_use('clam') # Experiment with 'clam', 'alt', 'default', 'classic'
        self.style.configure("green.Horizontal.TProgressbar",
                             troughcolor=ENTRY_BG, bordercolor=ENTRY_BG,
                             background=BUTTON_BG, lightcolor=BUTTON_BG, darkcolor=BUTTON_BG)

        # Configure default font
        default_font = tkFont.nametofont("TkDefaultFont")
        default_font.configure(family=FONT_FAMILY, size=FONT_SIZE_NORMAL)
        self.root.option_add("*Font", default_font)

    def _setup_gui(self):
        """Creates and arranges the GUI widgets."""
        self.root.title("Minecraft Launcher")
        # Adjust initial size slightly to accommodate potential settings panel
        self.root.geometry("850x600")
        self.root.configure(bg=BG_COLOR)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close) # Handle window close

        # --- Main Content Area ---
        # Use PanedWindow to allow resizing between main content and settings
        self.paned_window = tk.PanedWindow(self.root, orient=tk.HORIZONTAL, sashrelief=tk.RAISED, bg=BG_COLOR, bd=0)
        self.paned_window.pack(fill=tk.BOTH, expand=True)

        # --- Left Frame (Main Controls) ---
        left_frame = tk.Frame(self.paned_window, bg=BG_COLOR, width=500) # Give it an initial width
        left_frame.pack(fill=tk.BOTH, expand=True)
        self.paned_window.add(left_frame, stretch="always")

        # --- Input Section (inside left_frame) ---
        input_frame = tk.Frame(left_frame, bg=BG_COLOR)
        # Use pack within the left frame for centering vertically
        input_frame.pack(pady=(100, 20), padx=20) # Adjust padding

        nickname_label = tk.Label(input_frame, text="Nickname:", bg=BG_COLOR, fg=FG_COLOR, font=(FONT_FAMILY, FONT_SIZE_LARGE))
        nickname_label.pack(pady=(10, 5))

        self.nickname_var = tk.StringVar()
        self.nickname_entry = tk.Entry(input_frame, textvariable=self.nickname_var, width=35, # Slightly narrower
                                       bg=ENTRY_BG, fg=ENTRY_FG, relief=tk.FLAT,
                                       insertbackground=ENTRY_FG, font=(FONT_FAMILY, FONT_SIZE_NORMAL))
        self.nickname_entry.pack(pady=(0, 20))

        # --- Action Button (inside left_frame) ---
        self.action_button = tk.Button(left_frame, text="Install / Play / Update",
                                       command=self.start_action_thread,
                                       bg=BUTTON_BG, fg=BUTTON_FG, relief=tk.FLAT,
                                       activebackground=BUTTON_ACTIVE_BG, activeforeground=BUTTON_FG,
                                       font=(FONT_FAMILY, FONT_SIZE_LARGE), padx=20, pady=10)
        self.action_button.pack(pady=20) # Pack below input frame

        # --- Settings Toggle Button (Top Right of left_frame) ---
        settings_button = tk.Button(left_frame, text="⚙️ Settings",
                                    command=self.toggle_settings_frame,
                                    bg=BG_COLOR, fg=FG_COLOR, relief=tk.FLAT,
                                    activebackground=ENTRY_BG, activeforeground=FG_COLOR,
                                    font=(FONT_FAMILY, FONT_SIZE_NORMAL-1), bd=0)
        # Place it at the top-right corner of the left frame
        settings_button.place(relx=1.0, rely=0.0, x=-10, y=10, anchor=tk.NE)


        # --- Right Frame (Settings Panel - Initially hidden or added later) ---
        self.settings_frame = tk.Frame(self.paned_window, bg=ENTRY_BG, width=300) # Give initial width
        # Don't pack or add it initially, toggle function will handle it
        # self.paned_window.add(self.settings_frame, stretch="never") # Add but hide initially? Or add on toggle?

        # --- Settings Panel Content (inside self.settings_frame) ---
        settings_content_frame = tk.Frame(self.settings_frame, bg=ENTRY_BG, padx=15, pady=15)
        settings_content_frame.pack(fill=tk.BOTH, expand=True)

        settings_title = tk.Label(settings_content_frame, text="Settings", bg=ENTRY_BG, fg=FG_COLOR, font=(FONT_FAMILY, FONT_SIZE_LARGE))
        settings_title.pack(pady=(0, 15))

        # Gist URL
        gist_label = tk.Label(settings_content_frame, text="Config Gist URL:", bg=ENTRY_BG, fg=FG_COLOR)
        gist_label.pack(anchor=tk.W)
        self.gist_url_var = tk.StringVar()
        gist_entry = tk.Entry(settings_content_frame, textvariable=self.gist_url_var, width=35,
                              bg=BG_COLOR, fg=ENTRY_FG, relief=tk.FLAT, insertbackground=ENTRY_FG)
        gist_entry.pack(pady=(2, 10), fill=tk.X)

        # Max RAM
        ram_label = tk.Label(settings_content_frame, text="Max RAM (e.g., 4G):", bg=ENTRY_BG, fg=FG_COLOR)
        ram_label.pack(anchor=tk.W)
        self.max_ram_var = tk.StringVar()
        ram_entry = tk.Entry(settings_content_frame, textvariable=self.max_ram_var, width=10,
                             bg=BG_COLOR, fg=ENTRY_FG, relief=tk.FLAT, insertbackground=ENTRY_FG)
        ram_entry.pack(pady=(2, 10), anchor=tk.W)

        # Save Settings Button
        save_button = tk.Button(settings_content_frame, text="Save Settings",
                                command=self.save_settings,
                                bg=BUTTON_BG, fg=BUTTON_FG, relief=tk.FLAT,
                                activebackground=BUTTON_ACTIVE_BG, activeforeground=BUTTON_FG,
                                font=(FONT_FAMILY, FONT_SIZE_NORMAL), padx=10, pady=5)
        save_button.pack(pady=(15, 5))

        # --- Status Section (at the bottom, below the PanedWindow) ---
        status_frame = tk.Frame(self.root, bg=BG_COLOR)
        status_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=10)

        self.status_var = tk.StringVar()
        self.status_var.set("Ready.")
        self.status_label = tk.Label(status_frame, textvariable=self.status_var, wraplength=760, # Adjust wrap?
                                     bg=BG_COLOR, fg=FG_COLOR, justify=tk.LEFT,
                                     font=(FONT_FAMILY, FONT_SIZE_NORMAL))
        self.status_label.pack(pady=(5, 5), fill=tk.X)

        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(status_frame, variable=self.progress_var, maximum=100, length=760, style="green.Horizontal.TProgressbar")
        self.progress_bar.pack(pady=(5, 10), fill=tk.X)


    def _load_initial_config(self):
        """Loads local config when the app starts and populates UI."""
        loaded_config = self.core.load_local_config() # Core now returns the whole dict
        self.nickname_var.set(loaded_config.get("nickname", ""))
        self.gist_url_var.set(loaded_config.get("gist_url", CONFIG_URL)) # Use constant as default
        self.max_ram_var.set(loaded_config.get("max_ram", "4G")) # Default to 4G

    def update_status_display(self, message: str, progress: Optional[float] = None, is_error: bool = False):
        """Updates the GUI status label and progress bar. Called by LauncherCore."""
        self.status_var.set(message)
        if progress is not None:
            self.progress_var.set(progress)
        # Change status label color on error
        self.status_label.config(fg="red" if is_error else FG_COLOR)
        self.root.update_idletasks() # Force GUI update

    def toggle_settings_frame(self):
        """Shows or hides the settings panel."""
        if self.settings_frame_visible:
            self.paned_window.remove(self.settings_frame)
            self.settings_frame_visible = False
        else:
            # Add the frame to the paned window
            # Add with stretch="never" so it doesn't resize automatically as much
            self.paned_window.add(self.settings_frame, stretch="never", width=300)
            self.settings_frame_visible = True
            # Force redraw/update might be needed
            self.root.update_idletasks()
            # Try setting sash position after adding
            # Note: Sash positions are tricky, might need adjustment or different approach
            try:
                 total_width = self.paned_window.winfo_width()
                 sash_pos = total_width - 300 # Position sash for 300px settings panel
                 if sash_pos > 0:
                      self.paned_window.sash_place(0, sash_pos, 0)
            except tk.TclError:
                 logging.warning("Could not place sash for settings panel.")


    def save_settings(self):
        """Validates and saves the settings from the UI."""
        nickname = self.nickname_var.get().strip() # Get current nickname too
        gist_url = self.gist_url_var.get().strip()
        max_ram = self.max_ram_var.get().strip().upper() # Standardize to uppercase

        # Basic Validation
        if not gist_url:
            self.update_status_display("Error: Gist URL cannot be empty.", is_error=True)
            return
        if not max_ram:
            self.update_status_display("Error: Max RAM cannot be empty.", is_error=True)
            return
        # Simple regex to check for number followed by G or M
        if not re.match(r"^\d+[GM]$", max_ram):
            self.update_status_display("Error: Invalid Max RAM format. Use e.g., '4G' or '1024M'.", is_error=True)
            return

        # Pass all settings to core save function
        if self.core.save_local_config(nickname=nickname, gist_url=gist_url, max_ram=max_ram):
            self.update_status_display("Settings saved successfully.")
            # Optionally hide settings panel after save
            # if self.settings_frame_visible:
            #     self.toggle_settings_frame()
        else:
            # Error message should be set by core.save_local_config
            pass

    def start_action_thread(self):
        """Starts the main installation/update/launch process in a separate thread."""
        nickname = self.nickname_var.get().strip()
        if not nickname:
            self.update_status_display("Error: Nickname cannot be empty.", is_error=True)
            return

        # Disable button immediately
        self.action_button.config(state=tk.DISABLED)
        self.update_status_display("Starting...", progress=0)

        # Create and start the background thread
        action_thread = threading.Thread(
            target=self._run_core_tasks_wrapper,
            args=(nickname,),
            daemon=True,
            name="LauncherMainThread"
        )
        action_thread.start()

    def _run_core_tasks_wrapper(self, nickname: str):
        """Wrapper function to run core tasks and handle button state."""
        global root # Ensure root is accessible for scheduling
        success = False # Default to failure
        try:
            success = self.core.run_tasks(nickname)
        except Exception as e:
            # Catch unexpected errors from the core logic itself
            logging.exception("Unhandled exception in LauncherCore execution.")
            # Ensure GUI update happens on main thread
            if 'root' in globals():
                 root.after(0, self.update_status_display, f"Critical Error: {e}", None, True)
            else:
                 self.update_status_display(f"Critical Error: {e}", None, True)
            success = False # Ensure button is re-enabled on unexpected core error

        # Re-enable button ONLY if launch was not successful or an error occurred
        if not success:
            # Schedule button re-enable on the main thread
            if 'root' in globals():
                 root.after(0, lambda: self.action_button.config(state=tk.NORMAL))

    def _on_close(self):
        """Handles window closing action."""
        logging.info("Launcher window closed by user.")
        # Optional: Add cleanup here if needed (e.g., stop running threads gracefully)
        self.root.destroy()


import re # Need regex for RAM validation

# --- Main Execution ---
if __name__ == "__main__":
    root = tk.Tk()
    app = LauncherApp(root)
    root.mainloop()
    logging.info("="*10 + " Launcher Exited " + "="*10)
