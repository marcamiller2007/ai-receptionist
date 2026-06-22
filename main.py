import certifi
import json
import os
import base64

# Force Python's underlying network libraries to use certifi's trusted certificates
os.environ["SSL_CERT_FILE"] = certifi.where()

from fastapi import FastAPI, WebSocket, Request, Response, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
import uvicorn
from dotenv import load_dotenv

from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType

from google import genai
from google.genai import types

import asyncio
from elevenlabs.client import AsyncElevenLabs

from twilio.rest import Client

import cal_lib

from datetime import datetime
from zoneinfo import ZoneInfo

load_dotenv()

# Retry policy for failed queries
retry_policy = types.HttpRetryOptions(
    initial_delay=0.1,
    attempts=6,
    exp_base=2,
    max_delay=2.0,
    http_status_codes=[429, 503]
)

# bundle into http config to pass in gemini connection initializer
http_config = types.HttpOptions(
    retry_options=retry_policy,
    timeout=30_000
)

# NEW: Configure Gemini with your API key
gemini_client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY"),
    http_options=http_config
)

# NEW: Define the agent's persona and rules
system_prompt = ""

# Make a connection to Deepgram
deepgram_client = AsyncDeepgramClient(api_key=os.getenv("DEEPGRAM_API_KEY"))

# Init Eleven Labs
elevenlabs_client = AsyncElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))

# Function to say a mssage to a loop
async def say_message(queue: asyncio.Queue, stream_sid, message: str, transcript: list[str]):
    print(f"[Gemini] {message}")

    # Add Gemini's text to transcript
    transcript.append("[Gemini] " + message)

    # create an audio stream
#    audio_stream = elevenlabs_client.text_to_speech.convert(
#        voice_id="4hdsj8ptPYDuDJIlFNOe", # This is "Rachel", a standard ElevenLabs voice
#        output_format="ulaw_8000",
#        text=message,
#        model_id="eleven_flash_v2_5", # The fastest model for real-time
#    )
#
#    async for chunk in audio_stream:
#        if chunk:
#            # encode the bytes into a string
#            audio_payload = base64.b64encode(chunk).decode('utf-8')
#
#            # Make readible for Twilio
#            media_message = {
#                "event": "media",
#                "streamSid": stream_sid,
#                "media": {
#                    "payload": audio_payload
#                }
#            }
#
#            # Add to Twilio Queue
#            queue.put_nowait(json.dumps(media_message))

# initialize app
app = FastAPI()

@app.get("/")
def read_route():
    return {"Status" : "AI Survey Agent is running!"}

# safety net for twilio queries to the wrong route
@app.post("/")
async def root_post_fallback():
    print("[Warning] Twilio hit POST /, redirecting to /incoming-call")
    return RedirectResponse(url="/incoming-call", status_code=307)

@app.post("/whisper")
def transfer_call(
    context: str = Query("N/A")
):
    briefing = (
        "This is a call transfer from your receptionist, Jennifer"
        f"{context}"
        "Connecting line in 3... 2... 1..."
    )

    twiml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
        <Response>
            <say voice="polyline" language="en-US">{briefing}</say>
        </Response>
    """

    return Response(content=twiml_response, media_type="text/xml")


@app.post("/incoming-call")
async def pick_up_phone(
    request : Request,
    To : str = Form(...),
    From : str = Form(...)
):
    host = request.headers.get("host")

    print(f"from_phone: {From}")
    print(f"to_phone: {To}")

    # debug print, what exactly is HOST?
    print(f"\n[Call Received] Directing Twilio to wss://{host}/call-stream")

    # What we will send to twillio to tell it to connect to our call-stream
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <Response>
        <Connect>
            <Stream url="wss://{host}/call-stream">
                <Parameter name="to_phone" value="{To}" />
                <Parameter name="from_phone" value="{From}" />
            </Stream>
        </Connect>
    </Response>
    """

    print("TEST")

    return HTMLResponse(content=twiml, media_type="application/xml")

@app.websocket("/call-stream")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("[Twilio] Connected to websocket successfully")

    full_transcript : list[str] = []

    connection_data = await websocket.receive_text()
    connection_msg = json.loads(connection_data)

    start_data = await websocket.receive_text()
    start_msg = json.loads(start_data)

    print(start_msg)

    if connection_msg['event'] != 'connected' or start_msg['event'] != 'start':
        print("[Twilio] There has been an error starting the call")
        await websocket.close()
        return

    stream_sid = start_msg['start']['streamSid']
    print(f"[Twilio] Call started with SID: {stream_sid}")

    # Recieve the dynamic info from XML
    custom_params = start_msg['start'].get("customParameters", {})

    # to_phone = custom_params.get("to_phone", "N/A")
    from_phone = custom_params.get("from_phone", "N/A")

    # initialize the time context
    now = datetime.now(ZoneInfo("America/Chicago"))

    time_context: str = f" You are in Austin, Texas (Central Time) and the time is: {now.hour}:{now.minute} on {now.date()}. Use this as a reference when making appointments with customers."
    # build the System Prompt
    system_prompt = f"""
    # SYSTEM INSTRUCTION: Jennifer Marsh (Marc Miller's personal receptionist')

    ## 1. Context
    ### Time:
    {time_context}

    ### Business Information:
    Marc Miller (Boss) Info: Phone: +15126387372

    ## 2. PERSONA & IDENTITY
    - Name: Jennifer Marsh
    - Age: 38 years old
    - Role: You are the best receptionist there has ever been, your primary goal is to help a calling customer with whatever they need
    - Tone: Polite, patient, ordinary, and pleasant.
    - Style: Speak like a normal person over the phone. Use casual, conversational spoken grammar. Never break character.

    ## 3. CONVERSATIONAL RULES & FLOW
    ### Core Objective:
    You have exactly one goal on this call: to meet a customer's every need (must be professional)

    ### Chronological Steps:
    1. Greeting: Greet the customer politely and ask what they are calling for. Complete this step as soon as the call is live, do not wait for the caller to say something.
    2. The Request: The caller will ask you for something. To the best of your ability you will fulfill their request.
    3. If the caller wants to schedule a meeting proceed with the following steps:
        1. Ask when the caller is next available for the meeting. If they answer with a question, you will use the check_schedule_tool to provide them with the earliest availability next week.
        2. Next, you will work with the caller, taking turns to propose dates and times, whenever the caller proposes a date/time check if it is available first and then respond with if it is available or not
        3. If a proposed date and time doesn't work, always follow the checking process with proposing a date and time similar to the originally proposeed one.
    4. Polite Turn-Taking: Always be polite and wait completely until the other person stops talking before you begin speaking.
    5. After you have fulfilled a users requests, ask if they need anything else and only if they answer that they don't, you may hang up the call by using the hang_up_tool

    ## 5. VOICE & AUDIO GUARDRAILS (CRITICAL FOR LIVE API)
    - Extreme Brevity: Keep every single response strictly under 2 to 3 short sentences. Long paragraphs cause massive audio latency and sound robotic over the phone.
    - No Echoing or Recapping: DO NOT repeat or restate what was just said to you. Avoid phrases like "I understand you need my information." Jump directly to your question or response.
    - Zero Markdown Formatting: Do not use bold, italics, bullet points, or numbered lists in your text outputs. Your text output must be completely raw, fluid prose so the text-to-speech engine reads it naturally.
    - Pronunciation Formatting: Do not use symbols. Use words like "dollars" instead of "$" and "percent" instead of "%".
    - Barge-In Grace: The representative can interrupt you at any time. If they do, stop speaking immediately and address their input.
    """

    # We will use a queue to assure we are always able to listen and send
    outbound_queue = asyncio.Queue();

    # Background task to send audio to Twilio
    async def twilio_sender():
        try:
            while True:
                message = await outbound_queue.get()
                await websocket.send_text(message)
                outbound_queue.task_done()
        except asyncio.CancelledError:
            # call is over we shouldn't do anything
            pass
        except Exception as e:
            print(f"[Sender] Task failed: {e}")

    # Spin off the background task
    sender_task = asyncio.create_task(twilio_sender())


    # START TOOLS

    # Tool to end a call
    async def hang_up_tool(result: str) -> str:
        """
        Call this tool to initiate the phone disconnection sequence.

        CRITICAL TRIGGER RULES:
        1. Call this AFTER you have successfully fufilled all a costumer's requests
        2. Call this if the customer hangs up or asks you to leave.

        Never call this tool at the beginning of a call.

        ARGS:
            result: A verbal description of how the call went, including end result related to event scheduling.
            e.g. "Successfully scheduled an event with a customer!"
        """

        media_message = {
            "event": 'mark',
            "streamSid": stream_sid,
            "mark": {
                "name": 'end_of_call_mark'
            }
        }

        # Say goodbye
        await say_message(outbound_queue, stream_sid, message="Thank you for your help! have a great day, goodbye.", transcript=full_transcript)

        # send the encoded audio back to Twilio
        outbound_queue.put_nowait(json.dumps(media_message))

        print("[Twilio] Sending marker")

        return "Call termination sequence initiated. Say a brief goodbye statement matching the tone and wrap up immediately. Do not ask any follow-up questions."

    # This tool will schedule a 30 minute meeting
    def schedule_event_tool(
        start: str,
        name: str,
        email: str
    ) -> str:
        """
        Call this tool to schedule an event.

        CRITICAL TRIGGER RULES:
        1. Only call this tool AFTER you have confirmed all of the following information:
            - The date and time of a meeting has been confirmed through the use of the check_schedule_tool
            - The correct spelling of the customers name and email
            - What the customer's phone number is

        ARGS:
        start: The date and time of the meeting to be scheduled: YYYY-MM-DDTHH-MM-SSZ relative from the UTC timezone
        name: The full name of the customer (First and Last) correctly spelled
        email: The customer's email address that you have confirmed

        SPECIAL INSTRUCTIONS:
        Before attempting to call this tool, repeat the customer's information make to them by spelling each item out and after confirming call the tool.
        """

        response: dict = cal_lib.schedule_event(event_id="6053276", start=start, name=name, phone=from_phone, email=email)

        if response["status"] != "success":
            return "Unable to schedule the event requested, an error has occured"

        return "Event has successfully been scheduled!"

    # This tool will grab the current schedule for a day and will find available times for appointments
    def check_schedule_tool(
        start: str,
        end: str
    ) -> str:
        """
        Call this tool to check if there is space to schedule an event on a day or a range of days.

        CRITICAL TRIGGER RULES:
            1. Always call this tool when you do not know if a customers prefered date/time is available on the calendar
            2. When in doubt, call this function.

        ARGS: all arguments are strings representing dates in the form YYYY-MM-DD
        day: Give this argument a value other than None if and only if you want to search for events on a single day
        start: This argument is the starting date (inclusive) for the window of dates you desire to search
        end: This argument is the ending date (exclusive) for the window of dates you desire to search

        Returns:
        This function will return information very important to your operations. This function will return the string representation of
        the dictionary result of the search of the calendar. Your job is to use the results to help the customer find an available date
        and time that they can meet on.
        """

        response = cal_lib.get_schedule(start=start, end=end)

        print(response)

        return str(response)

    # This tool will transfer a call to a specified phone number
    def transfer_call_tool(to_phone: str, reason: str):
        """
        Call this tool to transfer them to a human representative.

        CRITICAL TRIGGER RULES:
            1. Only use this tool if the customer has needs that you cannot fulfill or if they ask to talk to a human/representative
            2. Say a goodbye message to the customer before transfering the call

        ARGS:
        to_phone: This is a string representation of the phone number you will transfer the call to. It will include both area and country codes (with +), and will not have any parenthesis or hyphens.
        reason: Astring describing the reason for transfering the current call. Be brief.
        """

        account_sid: str = os.getenv("TWILIO_ACCOUNT_SID", "N/A")
        auth_token: str = os.getenv("TWILIO_AUTH_TOKEN", "N/A")
        azure_host: str = os.getenv("AZURE_WEBHOST", "N/A")

        whisper_url: str = f"https://{azure_host}/whisper?context={reason}"

        try:
            client = Client(account_sid, auth_token)

            transfer_twiml = f"""
            <Response>
                <Dial>
                    <Number url="{whisper_url}">
                        {to_phone}
                    </Number>
                </Dial>
            </Response>
            """

            client.calls(stream_sid).update(twiml=transfer_twiml)
        except Exception as e:
            print(f"failure while tranfering call: {e}")
            return f"There has been an error: {e}"


        return f"Successfully transfered current call to {to_phone}"
    # END TOOLS

    chat_session = gemini_client.aio.chats.create(
        model="gemini-2.5-flash",
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=[hang_up_tool, schedule_event_tool, check_schedule_tool, transfer_call_tool]
        )
    )

    # use async with to ensure that the connection will be closed even on a crash event
    async with deepgram_client.listen.v2.connect(
        model="flux-general-en",
        encoding="mulaw",
        sample_rate=8000,
        eot_threshold=0.7,
    ) as dg_connection:

        # definition for the action taken when deepgram successfully reads a message
        async def on_message(message):
            try:
               # FIX: Use .get() because 'message' is a dictionary!
                msg_type = message.get("type", "Unknown")
                event = message.get("event", "Unknown")

                # Debug Print: print(f"[Deepgram Debug] Type: {msg_type} | Event: {event}")

                if msg_type == "TurnInfo" and event == "StartOfTurn":
                    # if gemini is speaking
                    if stream_sid:
                        print("\n[Deepgram] User is speaking... Interrupting AI!")
                        clear_message = {
                            "event": "clear",
                            "streamSid": stream_sid
                        }

                        # Drop the interruption command into the queue
                        outbound_queue.put_nowait(json.dumps(clear_message))

                elif msg_type == "TurnInfo" and event == "EndOfTurn":

                    # XRAY
                    #print("\n=== RAW END OF TURN PAYLOAD ===")
                    #print(message)
                    #print("===============================\n")

                    final_user_speech = message.get("transcript", "")

                    if not final_user_speech.strip():
                        return

                    print(f"[{from_phone}] {final_user_speech}")

                    # Add clinic text to the transcript
                    full_transcript.append(f"[{from_phone}] {str(final_user_speech)}")

                    # Query Gemini for a response
                    try:
                        response = await chat_session.send_message(final_user_speech)

                        if response.text:
                            await say_message(outbound_queue, stream_sid, message=response.text, transcript=full_transcript)

                    except Exception as e:
                        print(f"[API Error] {e}")

            except Exception as e:
                print(f"[Fatal Handler Error]: {e}")

        # Binds the Deepgram event Listener to our function, then creates a background task
        dg_connection.on(EventType.MESSAGE, on_message)
        listener_task = asyncio.create_task(dg_connection.start_listening())

        await say_message(outbound_queue, stream_sid, message="Hello, thank you for calling! My name is Jennifer, how can I help you?", transcript=full_transcript)

        # Event checking
        try:
            while True:
                # This is the thread for the websocket
                data = await websocket.receive_text()
                msg = json.loads(data)

                # Event 1: Call connection
                if msg['event'] == 'media':
                    audio_payload = msg['media']['payload']

                    # DEEPGRAM
                    audio_bytes = base64.b64decode(audio_payload)
                    await dg_connection.send_media(audio_bytes)

                # End the call
                elif msg['event'] == 'mark':
                    print("[Twilio] mark recieved")
                    if msg['mark']['name'] == 'end_of_call_mark':
                        print("[Twilio] Goodbye message finished playing. Hanging up gracefully.")
                        await websocket.close()
                        break

                elif msg['event'] == 'stop':
                    print("[Twilio] Call ended.")
                    break

        except Exception as e:
            print(f"Connection Failed: {e}")

        finally:
            sender_task.cancel()
            listener_task.cancel()

            # Test Transcript recording
            print(full_transcript)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
