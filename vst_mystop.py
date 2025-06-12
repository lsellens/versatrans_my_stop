"""vst_mystop.py - A script to track school bus locations using the My Stop API."""

import logging
import math
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any

import requests


CONFIG_FILE = "vst_mystop.conf"
SCHOOL_LIST_URL = "https://mystopclientlistapi.azurewebsites.net/"
TARGET_DISTANCE_METERS = 82


def _common_headers() -> dict[str, str]:
    """Return common headers for API requests."""
    return {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
        "User-Agent": "Tyler My Stop/1.0.0.0",
        "Accept-Encoding": "identity",
        "Connection": "Keep-Alive",
    }


class ConfigManager:
    """Manages configuration loading, saving, and updating."""

    def __init__(self, config_file: str) -> None:
        self.config_file: str = config_file
        self.config: dict[str, str | None] = self.load_config()

    def load_config(self) -> dict[str, str | None]:
        """Load configuration from the .conf file."""
        config: dict[str, str | None] = {}
        if os.path.exists(self.config_file):
            with open(self.config_file, "r", encoding="utf-8") as file:
                for line in file:
                    if "=" in line:
                        key, value = line.strip().split("=", 1)
                        config[key] = value if value else None
        return config

    def save_config(self) -> None:
        """Save configuration to the .conf file."""
        with open(self.config_file, "w", encoding="utf-8") as file:
            for key, value in self.config.items():
                file.write(f"{key}={value if value else ''}\n")

    def get_config(self) -> dict[str, str | None]:
        """Return the loaded configuration."""
        return self.config

    def prompt_for_credentials(self) -> None:
        """Prompt for username and password if not already configured."""
        if not self.config.get("Username"):
            self.config["Username"] = input("Enter your username: ")
        if not self.config.get("Password"):
            self.config["Password"] = input("Enter your password: ")


class GeoUtils:
    """Utility functions for geographic calculations."""

    @staticmethod
    def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate the Haversine distance between two GPS coordinates."""
        r = 6371000  # Radius of Earth in meters
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)

        a = (
            math.sin(delta_phi / 2) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
        )
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        return r * c

    @staticmethod
    def degrees_to_direction(degrees: float) -> str:
        """Convert degrees to cardinal direction."""
        directions = [
            "N",
            "NNE",
            "NE",
            "ENE",
            "E",
            "ESE",
            "SE",
            "SSE",
            "S",
            "SSW",
            "SW",
            "WSW",
            "W",
            "WNW",
            "NW",
            "NNW",
            "N",
        ]
        index = int((degrees + 11.25) % 360 / 22.5)
        return directions[index]


class SchoolService:
    """Handles API interactions to fetch school data."""

    @staticmethod
    def get_all_school_list() -> list[dict[str, Any]]:
        """Fetch the list of schools from the API."""
        url = f"{SCHOOL_LIST_URL}api/ClientList/getall"
        headers = _common_headers()
        headers["Host"] = SCHOOL_LIST_URL.split("/")[2]

        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            if "Clients" in data:
                return data["Clients"]
            logging.error("API response missing 'Clients' key.")
            return []
        except requests.RequestException as e:
            logging.error("Failed to get school list: %s", e)
            return []

    def get_closest_school_list(
        self, lat: float, lon: float, distance: float
    ) -> list[dict[str, Any]]:
        """Fetch the list of closest schools from the API based on coordinates."""
        url = f"{SCHOOL_LIST_URL}api/ClientList/getclosest"
        headers = _common_headers()
        headers["Host"] = SCHOOL_LIST_URL.split("/")[2]

        payload = {
            "AppType": 1,
            "Latitude": lat,
            "Longitude": lon,
            "Distance": distance,
        }

        response = requests.post(url, json=payload, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return data.get("Clients", [])
        logging.error(
            "Failed to get closest school list. Status code: %s", response.status_code
        )
        return []

    @staticmethod
    def select_school(school_list: list[dict]) -> dict[str, str] | None:
        """Allow the user to select a school from the list."""
        if not school_list:
            logging.warning("No schools found.")
            return None

        print("Please select your school:")
        for i, school in enumerate(school_list):
            print(f"{i + 1}. {school['Account']}")

        while True:
            try:
                choice = (
                    int(input("Enter the number corresponding to your school: ")) - 1
                )
                if 0 <= choice < len(school_list):
                    selected_school = school_list[choice]
                    return {
                        "SchoolGUID": selected_school["Serial"],
                        "ServiceUrl": selected_school["ServiceUrl"],
                        "SchoolLatitude": selected_school["Latitude"],
                        "SchoolLongitude": selected_school["Longitude"],
                    }
                logging.warning("Invalid choice. Try again.")
            except ValueError:
                logging.warning("Please enter a valid number.")


@dataclass
class SessionInfo:
    """Data class to hold session information."""

    session_id: str | None = None
    login_guid: str | None = None
    record_id: str | None = None


@dataclass
class BusInfo:
    """Data class to hold bus information."""

    bus_id: str | None = None
    route_number: str | None = None
    stop_latitude: float | None = None
    stop_longitude: float | None = None


class BusTracker:
    """Handles login, session management, and bus tracking."""

    def __init__(self, config: dict[str, str | None]) -> None:
        """Initializes a new BusTracker instance."""
        self.config: dict = config
        self.session: requests.Session = requests.Session()
        self.session_info = SessionInfo()
        self.bus_info = BusInfo()

    def _handle_api_error(self, message: str, exception: Exception) -> None:
        """Log API error and reset bus ID."""
        logging.error(f"{message}: %s", exception)
        self.bus_info.bus_id = None

    def login_user(self) -> tuple[float | None, float | None, str | None, str | None]:
        """Log in to the service and return session details."""
        url = f"{self.config['ServiceUrl']}api/admin/loginuser"
        headers = _common_headers()
        headers["Host"] = self.config["ServiceUrl"].split("/")[2]

        payload = {
            "UserName": self.config["Username"],
            "Password": self.config["Password"],
            "DeviceId": self.config["DeviceID"],
            "DeviceType": "Script",
            "DeviceName": "Home-Assistant",
            "SchoolGUID": self.config["SchoolGUID"],
        }

        # Send login request
        try:
            response = self.session.post(url, json=payload, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()

            # API response validation
            if "Students" not in data or not data["Students"]:
                logging.error("API response missing 'Students' or empty.")
                self.bus_info.bus_id = None
                return None, None, None, None

            # Check if bus is running and extract details
            matched_bus_data = data["Students"][0].get("MatchedBusData")
            if not matched_bus_data:
                logging.error("API response missing 'MatchedBusData' or it is None.")
                self.bus_info.bus_id = None
                return None, None, None, None

            isactive = matched_bus_data.get("IsActive", False)

            if not isactive:
                self.bus_info.bus_id = None
                return None, None, None, None

            self.session_info.session_id = data.get("SessionID", None)
            self.session_info.login_guid = data.get("LoginGUID", None)
            self.session_info.record_id = data["Students"][0].get("RecordID", None)
            self.bus_info.bus_id = matched_bus_data.get("RPVehicleId", None)
            matched_route = data["Students"][0].get("MatchedRoute", {})
            self.bus_info.route_number = matched_route.get("Route", None)
            self.bus_info.stop_latitude = matched_route.get("StopLatitude", None)
            self.bus_info.stop_longitude = matched_route.get("StopLongitude", None)

            # Bus current location
            latitude = matched_bus_data.get("Latitude", None)
            longitude = matched_bus_data.get("Longitude", None)
            heading = matched_bus_data.get("Heading", None)
            logtime = matched_bus_data.get("LogTime", None)

            return latitude, longitude, heading, logtime
        except requests.RequestException as e:
            self._handle_api_error("Login failed", e)
            return None, None, None, None
        except (KeyError, TypeError) as e:
            self._handle_api_error("Unexpected error during login", e)
            return None, None, None, None

    def vehicledata(self) -> tuple[float | None, float | None, str | None, str | None]:
        """Fetch the latest vehicle data for the bus."""

        url = f"{self.config['ServiceUrl']}api/student/vehicledata"
        headers = _common_headers()
        headers["Host"] = self.config["ServiceUrl"].split("/")[2]
        if self.session_info.session_id is not None:
            headers["X-SID"] = self.session_info.session_id

        payload = {"VehicleId": self.bus_info.bus_id}

        response = self.session.post(url, json=payload, headers=headers)

        if response.status_code == 200:
            data = response.json()

            # Check if bus is running
            isactive = data["StuBusData"].get("IsActive", False)

            if not isactive:
                self.bus_info.bus_id = None
                return None, None, None, None

            # Extract the relevant fields
            latitude = data["StuBusData"].get("Latitude", None)
            longitude = data["StuBusData"].get("Longitude", None)
            heading = data["StuBusData"].get("Heading", None)
            logtime = data["StuBusData"].get("LogTime", None)
            return latitude, longitude, heading, logtime
        logging.error(
            "Failed to fetch vehicle data. Status code: %s", response.status_code
        )
        self.bus_info.bus_id = None
        return None, None, None, None

    def recentvehicledata(self,) -> tuple[float | None, float | None, str | None, str | None]:
        """Fetch recent vehicle data for the bus."""

        url = f"{self.config['ServiceUrl']}api/student/recentvehicledata?rpVehicleId={
            self.bus_info.bus_id}"
        headers = _common_headers()
        headers["Host"] = self.config["ServiceUrl"].split("/")[2]
        if self.session_info.session_id is not None:
            headers["X-SID"] = self.session_info.session_id

        response = self.session.post(url, headers=headers, data="null")

        if response.status_code == 200:
            data = response.json()
            if data and "BusData" in data:
                # Extract the second data point (most recent one)
                bus_info = data.get("BusData")[1]
                # Convert HeadingDegrees to a direction
                direction = GeoUtils.degrees_to_direction(bus_info["HeadingDegrees"])
                # Extract relevant fields
                latitude = bus_info.get("Latitude", None)
                longitude = bus_info.get("Longitude", None)
                heading = direction
                logtime = bus_info.get("LogTime", None)
                return latitude, longitude, heading, logtime
            logging.error("No valid bus data available.")
            self.bus_info.bus_id = None
            return None, None, None, None
        logging.error(
            "Failed to fetch vehicle data. Status code: %s", response.status_code
        )
        self.bus_info.bus_id = None
        return None, None, None, None

    def check_bus_status(
        self,
    ) -> tuple[float | None, float | None, str | None, str | None]:
        """Helper function to check bus status and re-login if necessary."""
        latitude, longitude, heading, logtime = self.login_user()
        while not self.bus_info.bus_id or latitude is None or longitude is None:
            logging.warning("Bus is not currently running.")
            time.sleep(300)
            latitude, longitude, heading, logtime = self.login_user()
        return latitude, longitude, heading, logtime

    def track_bus(self, target_distance_meters: float) -> None:
        """Main loop to check the bus location and status."""

        latitude, longitude, heading, logtime = self.check_bus_status()

        # Continuously track bus until it reaches the target
        while True:
            if (
                self.bus_info.stop_latitude is not None
                and self.bus_info.stop_longitude is not None
                and latitude is not None
                and longitude is not None
            ):
                distance_to_target = GeoUtils.haversine_distance(
                    latitude,
                    longitude,
                    self.bus_info.stop_latitude,
                    self.bus_info.stop_longitude,
                )
                print(f"Distance to target: {distance_to_target}")
            else:
                logging.warning(
                    "Stop latitude or longitude is not set, or bus location is unknown." \
                    "Cannot calculate distance to target."
                )
                distance_to_target = None

            print(
                f"Latitude: {latitude}, Longitude: {longitude}, "
                f"Direction: {heading}, LogTime: {logtime}"
            )

            if (
                distance_to_target is not None
                and distance_to_target < target_distance_meters
            ):
                print("Bus is at bus stop.")
                break

            time.sleep(33)

            # Update bus location data
            latitude, longitude, heading, logtime = self.vehicledata()

    def student_scans(self) -> list[dict[str, Any]]:
        """Fetch student scans for the current record."""

        url = f"{self.config['ServiceUrl']}api/student/studentscans"
        headers = _common_headers()
        headers["Host"] = self.config["ServiceUrl"].split("/")[2]
        if self.session_info.session_id is not None:
            headers["X-SID"] = self.session_info.session_id

        payload = {"StuRecordList": [{"RecordID": self.session_info.record_id}]}

        # Send login request
        response = self.session.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            data = response.json()
            try:
                scans = data["Students"][0]["StudentScans"][0]["Scans"]
                return scans
            except (KeyError, TypeError, IndexError):
                # Handle potential missing keys gracefully
                logging.warning("Error accessing scan data. Returning empty list.")
                return []
        else:
            logging.error(
                "Failed to fetch student scans. Status code: %s", response.status_code
            )
            return []


def main() -> None:
    """Main function to run the bus tracking script."""
    try:
        # Setup logging
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
        )

        # Load or initialize config
        config_manager = ConfigManager(CONFIG_FILE)

        # Prompt for username and password if not already configured
        config_manager.prompt_for_credentials()

        # Generate a random device ID and store it
        if not config_manager.config.get("DeviceID"):
            config_manager.config["DeviceID"] = str(uuid.uuid4())

        # Initialize bus tracker
        bus_tracker = BusTracker(config_manager.get_config())

        # Check if 'SchoolGUID' and 'ServiceUrl' are already in the config
        if (
            "SchoolGUID" not in config_manager.config
            or "ServiceUrl" not in config_manager.config
            or "SchoolLatitude" not in config_manager.config
            or "SchoolLongitude" not in config_manager.config
        ):
            # Fetch school list and allow user to select a school
            school_list = SchoolService.get_all_school_list()
            if not school_list:
                logging.error("Failed to retrieve school list.")
                return

            school_info = SchoolService.select_school(school_list)
            if not school_info:
                logging.error("No school selected. Exiting.")
                return

            # Update configuration with selected school details
            config_manager.config.update(school_info)

        # Save updated config to file
        config_manager.save_config()

        # Log in and start tracking
        bus_tracker.track_bus(target_distance_meters=TARGET_DISTANCE_METERS)
    except KeyboardInterrupt:
        logging.info("Exiting on user request (KeyboardInterrupt).")
    except (ValueError, TypeError, requests.RequestException) as e:
        logging.error("Fatal error: %s", e)


if __name__ == "__main__":
    main()
