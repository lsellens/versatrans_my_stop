import math
import os
import time
import uuid

import requests

CONFIG_FILE = "vst-mystop.conf"
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
        self.config: dict = self.load_config()

    def load_config(self) -> dict[str, str | None]:
        """Load configuration from the .conf file."""
        config = {}
        if os.path.exists(self.config_file):
            with open(self.config_file, "r") as file:
                for line in file:
                    if "=" in line:
                        key, value = line.strip().split("=", 1)
                        config[key] = value if value else None
        return config

    def save_config(self) -> None:
        """Save configuration to the .conf file."""
        with open(self.config_file, "w") as file:
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
        R = 6371000  # Radius of Earth in meters
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)

        a = (
            math.sin(delta_phi / 2) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
        )
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        return R * c

    @staticmethod
    def degrees_to_direction(degrees: float) -> str:
        """Convert degrees to cardinal direction."""
        directions = [
            "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW", "N"
        ]
        index = int((degrees + 11.25) % 360 / 22.5)
        return directions[index]


class SchoolService:
    """Handles API interactions to fetch school data."""

    @staticmethod
    def get_all_school_list() -> list[dict[str, any]]:
        """Fetch the list of schools from the API."""
        url = f"{SCHOOL_LIST_URL}api/ClientList/getall"
        headers = _common_headers()
        headers["Host"] = SCHOOL_LIST_URL.split("/")[2]

        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            return data.get("Clients", [])
        else:
            print(f"Failed to get school list. Status code: {response.status_code}")
            return []

    @staticmethod
    def get_closest_school_list(
        lat: float, lon: float, distance: float
    ) -> list[dict[str, any]]:
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

        response = requests.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            data = response.json()
            return data.get("Clients", [])
        else:
            print(
                f"Failed to get closest school list. Status code: {response.status_code}"
            )
            return []

    @staticmethod
    def select_school(school_list: list[dict]) -> dict[str, str] | None:
        """Allow the user to select a school from the list."""
        if not school_list:
            print("No schools found.")
            return {}

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
                else:
                    print("Invalid choice. Try again.")
            except ValueError:
                print("Please enter a valid number.")


class BusTracker:
    """Handles login, session management, and bus tracking."""

    def __init__(self, config: dict) -> None:
        """Initializes a new BusTracker instance."""
        self.config: dict = config
        self.session: requests.Session = requests.Session()
        self.session_id: str | None = None
        self.record_id: str | None = None
        self.bus_id: str | None = None
        self.stop_latitude: float | None = None
        self.stop_longitude: float | None = None
        self.login_guid: str | None = None
        self.route_number: str | None = None

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
        response = self.session.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            data = response.json()

            # Check if bus is running and extract details
            matched_bus_data = data["Students"][0].get("MatchedBusData", {})

            if matched_bus_data:
                isactive = matched_bus_data.get("IsActive", False)
            else:
                isactive = False


            if not isactive:
                self.bus_id = None
                return None, None, None, None

            self.session_id = data.get("SessionID", None)
            self.login_guid = data.get("LoginGUID", None)
            self.record_id = data["Students"][0].get("RecordID", None)
            self.bus_id = data["Students"][0]["MatchedBusData"].get("RPVehicleId", None)
            self.route_number = data["Students"][0]["MatchedRoute"].get("Route", None)
            self.stop_latitude = data["Students"][0]["MatchedRoute"].get(
                "StopLatitude", None
            )
            self.stop_longitude = data["Students"][0]["MatchedRoute"].get(
                "StopLongitude", None
            )
            # Bus current location
            latitude = data["Students"][0]["MatchedBusData"].get("Latitude", None)
            longitude = data["Students"][0]["MatchedBusData"].get("Longitude", None)
            heading = data["Students"][0]["MatchedBusData"].get("Heading", None)
            logtime = data["Students"][0]["MatchedBusData"].get("LogTime", None)

            return latitude, longitude, heading, logtime
        else:
            print(f"Login failed with status code {response.status_code}")
            self.bus_id = None
            return None, None, None, None

    def vehicledata(self) -> tuple[float | None, float | None, str | None, str | None]:
        """Fetch the latest vehicle data for the bus."""
        url = f"{self.config['ServiceUrl']}api/student/vehicledata"
        headers = _common_headers()
        headers["Host"] = self.config["ServiceUrl"].split("/")[2]
        headers["X-SID"] = self.session_id

        payload = {"VehicleId": self.bus_id}

        response = self.session.post(url, json=payload, headers=headers)

        if response.status_code == 200:
            data = response.json()

            # Check if bus is running
            isactive = data["StuBusData"].get("IsActive", False)

            if not isactive:
                self.bus_id = None
                return None, None, None, None

            # Extract the relevant fields
            latitude = data["StuBusData"].get("Latitude", None)
            longitude = data["StuBusData"].get("Longitude", None)
            heading = data["StuBusData"].get("Heading", None)
            logtime = data["StuBusData"].get("LogTime", None)
            return latitude, longitude, heading, logtime
        else:
            print(f"Failed to fetch vehicle data. Status code: {response.status_code}")
            self.bus_id = None
            return None, None, None, None

    def recentvehicledata(
        self,
    ) -> tuple[float | None, float | None, str | None, str | None]:
        """Fetch the latest vehicle data for the bus."""
        url = f"{self.config['ServiceUrl']}api/student/recentvehicledata?rpVehicleId={self.bus_id}"
        headers = _common_headers()
        headers["Host"] = self.config["ServiceUrl"].split("/")[2]
        headers["X-SID"] = self.session_id

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
            else:
                print("No valid bus data available.")
                self.bus_id = None
                return None, None, None, None
        else:
            print(f"Failed to fetch vehicle data. Status code: {response.status_code}")
            self.bus_id = None
            return None, None, None, None

    def track_bus(self, target_distance_meters: float) -> None:
        """Main loop to check the bus location and status."""

        # Initial login and data retrieval
        latitude, longitude, heading, logtime = self.login_user()

        # Check if bus is running and wait if not
        while not self.bus_id or latitude is None or longitude is None:
            print("Bus is not currently running.")
            time.sleep(300)
            latitude, longitude, heading, logtime = self.login_user()

        # Track bus using initial data
        distance_to_target = GeoUtils.haversine_distance(
            latitude, longitude, self.stop_latitude, self.stop_longitude
        )
        print(f"Distance to target: {distance_to_target}")
        print(
            f"Latitude: {latitude}, Longitude: {longitude}, Direction: {heading}, LogTime: {logtime}"
        )
        time.sleep(33)
        while True:
            latitude, longitude, heading, logtime = self.vehicledata()
            # Check for bus inactivity and re-login if necessary
            while not self.bus_id or latitude is None or longitude is None:
                print("Bus is not currently running.")
                time.sleep(300)
                latitude, longitude, heading, logtime = self.login_user()

            distance_to_target = GeoUtils.haversine_distance(
                latitude, longitude, self.stop_latitude, self.stop_longitude
            )

            print(f"Distance to target: {distance_to_target}")
            print(
                f"Latitude: {latitude}, Longitude: {longitude}, Direction: {heading}, LogTime: {logtime}"
            )
            if distance_to_target < target_distance_meters:
                print("Bus is at bus stop.")
                # break
            time.sleep(33)

    def student_scans(self) -> list[dict[str, any]]:
        url = f"{self.config['ServiceUrl']}api/student/studentscans"
        headers = _common_headers()
        headers["Host"] = self.config["ServiceUrl"].split("/")[2]
        headers["X-SID"] = self.session_id

        payload = {"StuRecordList": [{"RecordID": self.record_id}]}

        # Send login request
        response = self.session.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            data = response.json()
            try:
                scans = data["Students"][0]["StudentScans"][0]["Scans"]
                return scans
            except (KeyError, TypeError, IndexError):
                # Handle potential missing keys gracefully
                print("Error accessing scan data. Returning empty list.")
                return []
        else:
            print(f"Failed to fetch student scans. Status code: {response.status_code}")
            return None

def main():
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
            print("Failed to retrieve school list.")
            return

        school_info = SchoolService.select_school(school_list)
        if not school_info:
            print("No school selected. Exiting.")
            return

        # Update configuration with selected school details
        config_manager.config.update(school_info)

    # Save updated config to file
    config_manager.save_config()

    # Log in and start tracking
    bus_tracker.track_bus(target_distance_meters=TARGET_DISTANCE_METERS)


if __name__ == "__main__":
    main()
