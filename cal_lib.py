import requests

from dotenv import load_dotenv
import os

load_dotenv()

api_key: str = os.getenv("CAL_API_KEY", "N/A")
authorization: str = "Bearer " + api_key

def schedule_event(
    event_id: str,
    start: str,
    name: str,
    phone: str,
    email: str
) -> dict:
    url = "https://api.cal.com/v2/bookings"

    payload = {
        "start": start,
        "attendee": {
            "name": name,
            "timeZone": "America/Chicago",
            "phoneNumber": phone,
            "language": "en",
            "email": email
        },
        "eventTypeId": event_id,
    }

    headers = {
        "cal-api-version": "2026-02-25",
        "Content-Type": "application/json",
        "Authorization": authorization
    }

    response = requests.post(url, json=payload, headers=headers)

    return response.json()

def get_schedule(
    day: str = "N/A",
    start: str = "N/A",
    end: str = "N/A"
) -> dict:
    if day == "N/A" and (start == "N/A" or end == "N/A"):
        return {
            "Error": 403,
            "description": "Invalid Arguments"
        }

    if day != "N/A":
        url: str = f"https://api.cal.com/v2/slots?eventTypeId=6053276&start={day}&end={day}&timeZone=America/Chicago"
    else:
        url: str = f"https://api.cal.com/v2/slots?eventTypeId=6053276&start={start}&end={end}&timeZone=America/Chicago"

    headers: dict = {
        "cal-api-version": "2024-09-04",
        "Authorization": authorization
    }

    response = requests.get(url=url, headers=headers)

    return response.json()

try:
    #response = schedule_event()
    response = get_schedule(day="2026-06-19", start="")

    print(response)
except Exception as e:
    print(f"ERROR : {e}")
