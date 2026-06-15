# AI Clinic Survey Voice Agent ("Janet Williams")

A fully asynchronous, real-time conversational AI voice agent designed to call health clinics and negotiate cash-pay pricing for outpatient procedures. The agent utilizes a seamless, low-latency pipeline to listen, think, speak, and securely log data to a PostgreSQL database just like a human assistant.

## 🚀 Key Features

* **True Asynchronous Architecture**: Built entirely on `asyncio` and FastAPI. The system uses a producer-consumer Queue architecture to ensure simultaneous listening, speaking, and database querying without ever blocking the event loop.
* **AI-Driven Turn Detection**: Utilizes Deepgram's modern **v2 Flux** endpoint. Instead of relying on rigid silence timers, the AI dynamically analyzes vocal pitch and grammatical structure via `StartOfTurn` and `EndOfTurn` events.
* **Seamless Human Barge-in**: The system instantly detects human interruption (`StartOfTurn`) and clears the Twilio audio queue, allowing the user to seamlessly talk over the AI.
* **Real-Time Audio Streaming**: Employs ElevenLabs' `AsyncElevenLabs` generator to stream synthesized audio chunks to Twilio the exact millisecond they are generated, eliminating conversational lag.
* **High-Performance Database Pooling**: Uses `asyncpg` and FastAPI lifespan contexts to manage a persistent pool of PostgreSQL connections, allowing the AI to save data mid-conversation with zero latency.
* **Agentic Tool Calling**: Gemini 2.5 Flash dynamically triggers asynchronous Python tools mid-conversation:
  * `push_data_tool`: Automatically extracts pricing and procedure data and pushes it to PostgreSQL.
  * `hang_up_tool`: Gracefully ends the call using Twilio `mark` events after completing the objective.

## 🛠️ Technology Stack

* **Backend**: Python 3.x, FastAPI, Uvicorn, Asyncio WebSockets
* **Telephony**: Twilio (TwiML & Media Streams)
* **Speech-to-Text (Ears)**: Deepgram (`listen.v2`, Flux model)
* **LLM (Brain)**: Google GenAI (`gemini-2.5-flash`, `.aio` asynchronous client)
* **Text-to-Speech (Mouth)**: ElevenLabs (`AsyncElevenLabs`, `eleven_flash_v2_5` streaming model)
* **Database**: PostgreSQL (`asyncpg` for raw async SQL execution)

## 📋 Prerequisites

To run this agent, you will need active accounts and API keys for the following services:
1. [Deepgram](https://deepgram.com/)
2. [Google AI Studio (Gemini)](https://aistudio.google.com/)
3. [ElevenLabs](https://elevenlabs.io/)
4. [Twilio](https://twilio.com/) (with an active phone number)
5. **PostgreSQL Database** (Local install, or cloud-hosted via Supabase, AWS, Neon, etc.)
6. [Ngrok](https://ngrok.com/) (for local tunneling and testing)

## ⚙️ Installation & Setup

### 1. Clone & Environment Setup
Clone the repository and activate a virtual environment:
```bash
git clone <your-repo-url>
cd call-agent
python -m venv venv
source venv/bin/activate  # On Windows use: venv\Scripts\activate
```

### 2. Install Dependencies
Ensure you are installing the modern v5+ Deepgram SDK, the new Google GenAI SDK, and the asyncpg driver.
```bash
pip install fastapi uvicorn websockets twilio deepgram-sdk google-genai elevenlabs python-dotenv certifi asyncpg
```

### 3. Database Initialization
Connect to your PostgreSQL database and execute the following SQL command to create the necessary table for the AI to push data to:
```sql
CREATE TABLE procedure_rates (
    id SERIAL PRIMARY KEY, 
    procedure_name VARCHAR(255), 
    price NUMERIC
);
```

### 4. Environment Variables
Create a `.env` file in the root directory and add your keys and database connection string:
```env
DEEPGRAM_API_KEY=your_deepgram_key_here
GEMINI_API_KEY=your_gemini_key_here
ELEVENLABS_API_KEY=your_elevenlabs_key_here
DATABASE_URL=postgresql://username:password@hostname:5432/database_name
```

## 🏃‍♂️ Running the Agent Locally

1. **Start the FastAPI Server**:
   ```bash
   uvicorn main:app --host 0.0.0.0 --port 8000
   ```

2. **Start your Ngrok Tunnel**:
   Twilio requires a public URL to reach your local server.
   ```bash
   ngrok http 8000
   ```

3. **Configure Twilio**:
   * Go to your Twilio Console -> Phone Numbers -> Manage -> Active Numbers.
   * Under **A Call Comes In**, set the webhook to your Ngrok URL followed by the route:
     `https://<your-ngrok-url>.ngrok-free.app/incoming-call`
   * Ensure it is set to `HTTP POST`.

4. **Make the Call**:
   Dial your Twilio phone number. You will hear Twilio bridge the connection, and Janet Williams will begin speaking!

## 🧠 Architecture Overview (The Main Loop)

1. **Initialization**: FastAPI's lifespan manager connects to PostgreSQL and creates an `asyncpg` connection pool.
2. **Connection**: Twilio initiates a call and opens a persistent WebSocket (`/call-stream`).
3. **Background Tasks**: Python spins off a `twilio_sender` task that waits by an `outbound_queue` mailbox, ready to fire audio to Twilio at any moment.
4. **Listening & Thinking**: 
   * An `async with` wrapper safely manages the Deepgram connection.
   * When Deepgram detects an `EndOfTurn`, it extracts the transcript and hands it to Gemini via `await chat_session.send_message()`.
5. **Taking Action**:
   * If Gemini determines the user revealed a price, it triggers `push_data_tool`, which safely grabs a Postgres connection from the pool and executes an `INSERT` statement in milliseconds.
6. **Streaming**: ElevenLabs streams the synthesized audio in chunks via an `async for` loop. These chunks are dropped into the `outbound_queue` and instantly sent to Twilio.

## 🐛 Troubleshooting

* **Silent Errors (Deepgram)**: If the call bridges but there is no response, ensure your `on_message` handler expects a single `message` argument and safely uses `.get("transcript", "")` as the payload is a dictionary.
* **Database Freezes**: Ensure you are using `await connection.execute(...)` inside your tools. Synchronous database queries will block the event loop and cause the call to drop audio.
* **Audio Stuttering**: Ensure your Uvicorn server isn't running other synchronous blocking tasks. The entire pipeline must remain `async`.
* **503 Gemini Errors**: Google endpoints can occasionally experience high traffic. The code handles this gracefully, but if persistent, swap the model to an older version (e.g., `gemini-2.0-flash`).
# ai-receptionist
