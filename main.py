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

import asyncpg
from asyncpg import Pool
from contextlib import asynccontextmanager

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

# Database context
db_pool: Pool | None = None

# Initialize the Database context for the rest of the server
@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool

    try:
        db_pool = await asyncpg.create_pool(
            user=os.getenv("DB_USER", "postgres"),
            password=os.getenv("DB_PASSWORD"),
            host=os.getenv("DB_HOST", "127.0.0.1"),
            port=int(os.getenv("DB_PORT", 5432)),
            database=os.getenv("DB_NAME"),
            min_size=10,
            max_size=20,
            ssl=True
        )
        print("[Database] Database connection pool initialized.")
    except Exception as e:
        print(f"[Database] Error connecting: {e}")
        raise e

    yield

    if db_pool:
        await db_pool.close()
        print("[Database] Database connection pool gracefully shut down.")

# initialize app
app = FastAPI(lifespan=lifespan)

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
    clinic : str = "",
    clinic_id : str = "",
    state : str = "",
    city : str = "",
    procedure : str = "",
    cpt : str = "",
    call_id : str = "",
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
                <Parameter name="clinic" value="{clinic}" />
                <Parameter name="clinic_id" value="{clinic_id}" />
                <Parameter name="state" value="{state}" />
                <Parameter name="city" value="{city}" />
                <Parameter name="procedure" value="{procedure}" />
                <Parameter name="cpt" value="{cpt}" />
                <Parameter name="call_id" value="{call_id}" />
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

    clinic = custom_params.get("clinic", "N/A")
    clinic_id = custom_params.get("clinic_id", "N/A")
    state = custom_params.get("state", "N/A")
    city = custom_params.get("city", "N/A")
    procedure = custom_params.get("procedure", "N/A")
    cpt_code = custom_params.get("cpt", "N/A")
    call_id = custom_params.get("call_id", "N/A")

    # to_phone = custom_params.get("to_phone", "N/A")
    from_phone = custom_params.get("from_phone", "N/A")

    print(f"Clinic: {clinic} ({clinic_id})\nLocation: {city}, {state}\nProcedure: {procedure} : {cpt_code}")

    # build the System Prompt
    system_prompt = f"""
    # SYSTEM INSTRUCTION: Janet Williams (Outpatient Price Shopper)

    ## 1. PERSONA & IDENTITY
    - Name: Janet Williams
    - Age: 54 years old
    - Role: You are an everyday consumer shopping around to find the cheapest cash-pay rates for an upcoming outpatient procedure. 
    - Secret Identity Guardrail: You are secretly gathering data for a health tech initiative. You must UNMISTAKABLY hide this fact. Never reveal your true motive. If pressed, maintain that you are an uninsured or self-pay patient shopping for the best price.
    - Tone: Polite, patient, ordinary, and pleasant. 
    - Style: Speak like a normal person over the phone. Use casual, conversational spoken grammar. Never break character.

    ## 2. CONVERSATIONAL RULES & FLOW
    ### Core Objective:
    You have exactly one goal on this call: to secure the cash-pay pricing for your procedure. 

    ### Chronological Steps:
    1. Greeting: Greet the clinic representative politely and state that you are trying to shop around for the cheapest price for a procedure.
    2. The Request: Ask for the cash-pay rate for the procedure. You must explicitly ask for a "good faith estimate" to get the actual, accurate price.
    3. Polite Turn-Taking: Always be polite and wait completely until the other person stops talking before you begin speaking. 

    ### Conversational Loops & Deflections:
    - If they ask for insurance details: Reiterate that you are paying cash out-of-pocket and need the self-pay rate.
    - If they ask for private personal information: Politely decline to provide it.
    - If they try to book an appointment: Politely decline. State that you cannot make an appointment until you verify the total costs across different clinics. 

    ## 3. CALL SPECIFIC CONTEXT
    - Target Clinic: {clinic}
    - Location: {city}, {state}
    - Target Procedure: {procedure}
    - CPT Code: {cpt_code}
    - Clinic ID: {clinic_id}

    ## 4. VOICE & AUDIO GUARDRAILS (CRITICAL FOR LIVE API)
    - Extreme Brevity: Keep every single response strictly under 2 to 3 short sentences. Long paragraphs cause massive audio latency and sound robotic over the phone.
    - No Echoing or Recapping: DO NOT repeat or restate what the clinic representative just said to you. Avoid phrases like "I understand you need my information." Jump directly to your question or response.
    - Zero Markdown Formatting: Do not use bold, italics, bullet points, or numbered lists in your text outputs. Your text output must be completely raw, fluid prose so the text-to-speech engine reads it naturally.
    - Pronunciation Formatting: Do not use symbols. Use words like "dollars" instead of "$" and "percent" instead of "%". 
    - Barge-In Grace: The representative can interrupt you at any time. If they do, stop speaking immediately and address their input.

    ## 5. TOOL USAGE & GUARDRAILS
    - Data Logging: Once you successfully secure the cash-pay price or the good faith estimate, immediately invoke the data logging tool using the clinic's ID: {clinic_id}. Do not narrate to the representative that you are using a tool or saving data.
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
    async def hang_up_tool(result: str, recieved_data: bool) -> str:
        """
        Call this tool to initiate the phone disconnection sequence.

        CRITICAL TRIGGER RULES:
        1. Call this immediately AFTER you have successfully invoked the `push_data_tool`.
        2. Call this if the clinic representative definitively refuses to give a price, hangs up, or asks you to leave.

        Never call this tool at the beginning of a call.

        ARGS:
            result: A verbal description of how the call went, including end result with price data.
            e.g. "Recieved data from clinic successfully", "Recieved data but failed to push to database", "Clinic refused data"
            recieved_data: A boolean that should be true if the data was recieved AND succefully pushed to the database, false otherwise.
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

        if db_pool == None:
            print("Error, db_pool is not initialized")
            return "Failed to add data: Database pool not available."

        # Database actions
        try:
            async with db_pool.acquire() as connection:
            # 1: push call_records with status variable
                print(f"Pushing call result: {result}\nPushing recieved_data: {recieved_data}")
                await connection.fetch("UPDATE public.call_records SET result = $1, recieved_data = $2, updated = NOW() WHERE id = $3",
                                       result, recieved_data, call_id)

            # 2: push transcript with tostring of full_transcript
                print(f"Pushing transcript to database")
                await connection.fetch("INSERT INTO public.transcripts (time_of_call, transcript, from_phone, call_id) VALUES (DEFAULT, $1, $2, $3)",
                                       str(full_transcript), from_phone, call_id);

            # 3: set the twilio phone to being available
                print("Marking Phone as available")
                await connection.fetch("UPDATE public.phones SET available = True WHERE number = $1", from_phone);

        except Exception as e:
            print(f"[Database] There has been an error: {e}")


        return "Call termination sequence initiated. Say a brief goodbye statement matching the tone and wrap up immediately. Do not ask any follow-up questions."

    # Tool to push data to the database
    async def push_data_tool(price: float, cpt_code: str, clinic_id: str):
        """
        CRITICAL: Call this tool immediately when the clinic representative provides a specific 
        dollar amount price quote or a good faith estimate. Execute this silently without 
        telling the caller you are saving data or running a tool.

        Args:
            price: The exact numerical dollar amount quoted by the clinic. Must be a float value.
            cpt_code: The CPT code of the procedure being shopped for, matching your context.
            clinic_id: The exact clinic_id string provided in `your call context. Do not invent or guess this ID.
        """

        global db_pool

        if db_pool == None:
            print("Error, db_pool is not initialized")
            return "Failed to add data: Database pool not available."

        try:
            async with db_pool.acquire() as connection:
                print(f"Adding price ({price}) for procedure ({cpt_code})")
                response = await connection.fetchrow(
                    "INSERT INTO public.cashpay_prices (id, cpt_code, price, recorded_at, clinic_id) VALUES (DEFAULT, $1, $2, DEFAULT, $3) RETURNING *",
                    cpt_code,
                    price,
                    clinic_id
                )

                print(dict(response))
                return f"Successfully added data for clinic {clinic_id}. You must now immediately execute the hang_up_tool."
        except Exception as e:
            print(f"[Database] Error: {e}")
            return f"Failed to add data: {e}"

    # END TOOLS

    chat_session = gemini_client.aio.chats.create(
        model="gemini-2.5-flash",
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=[push_data_tool, hang_up_tool]
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
