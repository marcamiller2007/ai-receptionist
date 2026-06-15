import os
import urllib.parse
from twilio.rest import Client
from dotenv import load_dotenv

load_dotenv();

TO_PHONE = "+15126387372"
FROM_PHONE = "+15129003364"

def make_call(to_phone, from_phone, query_params):
    # Make a connection to Twilio Client
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    account = os.getenv("TWILIO_ACCOUNT_SID")
    client = Client(account, auth_token)

    NGROK_HOST = "gestate-remover-output.ngrok-free.dev"

    # Create the call
    call = client.calls.create(
        method="POST",
        url=f"https://{NGROK_HOST}/incoming-call?{query_params}",
        to=to_phone,
        from_=from_phone
    )

    print(f"Call successfully initiated! Call SID: {call.sid}")

def make_params(clinic, clinic_id, state, city, procedure, cpt):
    query_params = urllib.parse.urlencode({
        "clinic": clinic,
        "clinic_id": clinic_id,
        "city": city,
        "state": state,
        "procedure": procedure,
        "cpt": cpt
    })

    print(f"Query Params: {query_params}")

    return query_params

# Start of Procedure
params = make_params(
    "Marcs goobers",
    "Some Code",
    "Texas",
    "Austin",
    "MRI Thingy",
    "23944"
)
make_call(TO_PHONE, FROM_PHONE, params)
