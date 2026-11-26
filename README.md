# Adaptive UI Engine
A Context-Aware Interface System Using Real-Time Behavioral Analysis
The Adaptive UI Engine is a real-time background assistant that transforms the static desktop experience into a reactive environment. By capturing global user interaction patterns—keyboard telemetry, mouse dynamics, and touchpad gestures—the engine uses a Finite State Machine (FSM) to dynamically adapt system overlays and behavioral feedback.

# 🧠 Core Engineering Concepts
1. Finite State Machine (FSM) Logic
The system classifies user behavior into six detected states, checked in this order on a 1.5 s moving average (the first match wins):

IDLE (Dark): more than 5 seconds of total inactivity; triggers "System Paused" overlay and energy-saving brightness.

FOCUS (Purple): sustained typing with low cursor movement; enters "Deep Work" mode with minimal distractions.

READING (Blue): scrolling with little typing, clicking or movement; calm full-width layout.

ERROR (Red): conflicting signals, such as heavy typing together with heavy mouse use; triggers assistance mode.

ACTIVE (Cyan): high-intensity navigation (clicks, fast movement or fast scrolling); triggers telemetry widgets and live stats.

LIGHT (Grey): some input that matches none of the above; no adaptation.

PAUSED is set from the tray and is never detected. A new state takes over only after winning for 1 second; pauses while reading or thinking keep FOCUS or READING instead of dropping to LIGHT. Confidence shows how far past its thresholds the input is, nudged by the foreground app (editors lean towards FOCUS, browsers towards READING).

On first launch the engine calibrates for two minutes while you work normally, then saves your thresholds to `config.json` (`%APPDATA%/AdaptiveUI` on Windows). Run `python main.py --recalibrate` to repeat it.

2. Global Event Interception
Unlike standard apps, this engine operates at the OS level using an Input Abstraction Layer. Using pynput and multi-threaded listeners, it captures:

Keyboard: Keys per second (KPS)

Mouse: Clicks per second and Manhattan Distance (dx + dy) movement estimation.

Touchpad: Scroll delta detection for advanced activity tracking.

# 🛠️ Technical Architecture
The project follows a 4-Layer Modular Pipeline for low-latency execution (<10ms):

Input Layer: High-frequency event listeners (Threaded).

Analysis Layer: Normalization, debouncing, and moving average smoothing to prevent data spikes.

FSM Engine: Decision-making layer using threshold-based behavioral confidence.

Output Layer: Non-blocking Tkinter overlay rendering engine with hardware-accelerated fade animations.

# 🚀 Key Bug Fixes & Optimizations
The "Scroll-Sync" Breakthrough
Problem: Early versions entered IDLE mode while users were actively scrolling through long documents, as standard listeners ignored scroll deltas.
Solution: Implemented Idle Timer Synchronization across all input vectors. Now, any scroll or touchpad gesture resets the global idle clock, ensuring seamless interaction during content consumption.

Anti-Flicker Debouncing
Problem: "State Jitter" occurred when interaction intensity hovered near a threshold.
Solution: Developed Candidate State Buffering. A state transition is only committed if the behavioral data persists for 2 consecutive cycles (1000ms), ensuring a stable UI experience.

# 📂 Project Structure
Plaintext
├── main.py              # System Orchestrator
├── event_capture.py     # Global Listeners (Keyboard/Mouse/Scroll)
├── state_engine.py      # FSM Logic & Thresholding
├── analyzer.py          # Metrics & Smoothing (Moving Averages)
├── overlay_manager.py   # Adaptive UI Rendering Engine
└── ui_controller.py     # Dashboard & Telemetry Visualization
# ⚡ Setup & Installation
**Clone the Repo:**

git clone https://github.com/nishantchakravarthy/Adaptive-UI.git

**Install Dependencies:**


pip install pynput customtkinter screeninfo Pillow

**Run the Engine:**

python main.py

**Run the Tests:**

pip install pytest
python -m pytest
# 👥 Authors
**Nishant Chakravarthy** - System Architecture & FSM Design

**Parth** - UI Design & OS Integration
