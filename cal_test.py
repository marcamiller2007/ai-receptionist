import requests

from dotenv import load_dotenv
import os

load_dotenv()

api_key: str = os.getenv("CAL_API_KEY", "N/A")
authorization: str = "Bearer " + api_key

def schedule_event() -> dict:
    url = "https://api.cal.com/v2/bookings"

    payload = {
        "start": "2026-06-19T16:00:00Z",
        "attendee": {
            "name": "John Doe",
            "timeZone": "America/Chicago",
            "phoneNumber": "+15126387372",
            "language": "en",
            "email": "marcm6530@gmail.com"
        },
        "eventTypeId": 6053276,
    }

    headers = {
        "cal-api-version": "2026-02-25",
        "Content-Type": "application/json",
        "Authorization": authorization
    }

    response = requests.post(url, json=payload, headers=headers)

    return response.json()

def get_schedule() -> dict:
    url: str = "https://api.cal.com/v2/slots?eventTypeId=6053276&start=2026-06-19&end=2026-06-19&timeZone=America/Chicago"

    headers: dict = {
        "cal-api-version": "2024-09-04",
        "Authorization": authorization
    }

    response = requests.get(url=url, headers=headers)

    return response.json()



try:
    #response = schedule_event()
    response = get_schedule()

    print(response)
except Exception as e:
    print(f"ERROR : {e}")
