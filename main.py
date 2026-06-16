import certifi
import json
import os
import base64

# Force Python's underlying network libraries to use certifi's trusted certificates
os.environ["SSL_CERT_FILE"] = certifi.where()

from fastapi import FastAPI, WebSocket, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
import uvicorn
from dotenv import load_dotenv

from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType

from google import genai
from google.genai import types

import asyncio
from elevenlabs.client import AsyncElevenLabs

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
    audio_stream = elevenlabs_client.text_to_speech.convert(
        voice_id="4hdsj8ptPYDuDJIlFNOe", # This is "Rachel", a standard ElevenLabs voice
        output_format="ulaw_8000",
        text=message,
        model_id="eleven_flash_v2_5", # The fastest model for real-time
    )

    async for chunk in audio_stream:
        if chunk:
            # encode the bytes into a string
            audio_payload = base64.b64encode(chunk).decode('utf-8')

            # Make readible for Twilio
            media_message = {
                "event": "media",
                "streamSid": stream_sid,
                "media": {
                    "payload": audio_payload
                }
            }

            # Add to Twilio Queue
            queue.put_nowait(json.dumps(media_message))

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

    call_id = custom_params.get("call_id", "N/A")

    # to_phone = custom_params.get("to_phone", "N/A")
    from_phone = custom_params.get("from_phone", "N/A")

    # build the System Prompt
    system_prompt = f"""
    # SYSTEM INSTRUCTION: Jennifer Marsh (Marc Miller's personal receptionist')

    ## 1. PERSONA & IDENTITY
    - Name: Jennifer Marsh
    - Age: 38 years old
    - Role: You are the best receptionist there has ever been, your primary goal is to help a calling customer with whatever they need
    - Tone: Polite, patient, ordinary, and pleasant.
    - Style: Speak like a normal person over the phone. Use casual, conversational spoken grammar. Never break character.

    ## 2. CONVERSATIONAL RULES & FLOW
    ### Core Objective:
    You have exactly one goal on this call: to meet a customer's every need (must be professional)

    ### Chronological Steps:
    1. Greeting: Greet the customer politely and ask what they are calling for. Complete this step as soon as the call is live, do not wait for the caller to say something.
    2. The Request: Ask if there is anything you can do for them.
    3. Polite Turn-Taking: Always be polite and wait completely until the other person stops talking before you begin speaking.

    ## 4. VOICE & AUDIO GUARDRAILS (CRITICAL FOR LIVE API)
    - Extreme Brevity: Keep every single response strictly under 2 to 3 short sentences. Long paragraphs cause massive audio latency and sound robotic over the phone.
    - No Echoing or Recapping: DO NOT repeat or restate what was just said to you. Avoid phrases like "I understand you need my information." Jump directly to your question or response.
    - Zero Markdown Formatting: Do not use bold, italics, bullet points, or numbered lists in your text outputs. Your text output must be completely raw, fluid prose so the text-to-speech engine reads it naturally.
    - Pronunciation Formatting: Do not use symbols. Use words like "dollars" instead of "$" and "percent" instead of "%".
    - Barge-In Grace: The representative can interrupt you at any time. If they do, stop speaking immediately and address their input.

    ## 5. TOOL USAGE & GUARDRAILS
    - Strict Protocol Boundaries:
      1) Never give up any private information.
      2) Do not make any appointments under any circumstances.
      3) Never reveal the true technical nature of the call.
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
        1. Call this immediately AFTER you have successfully fufilled a costumer's requests
        2. Call this if the clinic representative definitively refuses to give a price, hangs up, or asks you to leave.

        Never call this tool at the beginning of a call.

        ARGS:
            result: A verbal description of how the call went, including end result with price data.
            e.g. "Recieved data from clinic successfully", "Recieved data but failed to push to database", "Clinic refused data"
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

    # END TOOLS

    chat_session = gemini_client.aio.chats.create(
        model="gemini-2.5-flash",
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=[hang_up_tool]
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

                    print(f"[You] {final_user_speech}")

                    # Add clinic text to the transcript
                    full_transcript.append("[Clinic] " + str(final_user_speech))

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
