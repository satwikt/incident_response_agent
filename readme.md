# SRE Copilot ADK

This repository contains the SRE Copilot (built on Google GenAI) and a Demo application to showcase proactive monitoring, anomaly detection, and automated root cause analysis.

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop) installed and running.
- A **Google GenAI API Key** for the SRE Copilot.

---

## 1. Setting up the Demo Application

The Demo application is a mock FastAPI service instrumented with OpenTelemetry. It includes a complete observability stack (Prometheus, Loki, Tempo, Grafana).

1. Navigate to the `Demo` directory:
   ```bash
   cd Demo
   ```
2. Start the Demo stack using Docker Compose:
   ```bash
   docker-compose up -d
   ```
   *This will start the FastAPI application on `http://localhost:8000` along with the observability suite.*

---

## 2. Setting up the SRE Copilot

The Copilot runs alongside the Demo app, actively monitoring its telemetry and alerting on anomalies.

1. Navigate to the `Copilot` directory:
   ```bash
   cd Copilot
   ```
2. Set up the environment variables:
   ```bash
   cp .env.example .env
   ```
3. Open the `.env` file and configure the following:
   
   - **`GOOGLE_API_KEY`**: Provide your Google GenAI API key.
   - **`APP_SERVICE_NAME`**: Ensure this matches the target application's service name (the default for the demo is `todo-app`).

   **Optional Configurations:**
   - **Discord Notifications**: 
     - Create a webhook in Discord (Channel Settings → Integrations → Webhooks → New Webhook → Copy URL).
     - Set `DISCORD_WEBHOOK_URL=your_webhook_url`. 
     - *Note: If `DISCORD_WEBHOOK_URL` is left empty, the Copilot will simply skip sending notifications without erroring out.*
   - **Alert Thresholds**: You can tune the proactive watcher using the following variables:
     - `ALERT_ERROR_RATE_THRESHOLD`: The percentage error rate required to trigger an alert.
     - `ALERT_LATENCY_P95_MS`: P95 latency threshold in milliseconds.
     - `ALERT_SLOW_TRACE_COUNT` & `ALERT_SLOW_TRACE_MIN_MS`: Threshold for tracking slow request traces.
     - `ALERT_LOG_KEYWORDS`: Keywords in logs that trigger alerts (e.g., `ERROR,CRITICAL`).

4. Start the SRE Copilot stack:
   ```bash
   docker-compose up -d
   ```

---

## 3. Running the Demo

Once both stacks are running, you can interact with the environment using the following URLs:

- **Demo Application**: `http://localhost:8000`
- **Grafana Dashboard**: `http://localhost:3000` (Login is not required, anonymous admin access is enabled)
- **Copilot API**: `http://localhost:8001`

### Testing the Copilot (Injecting Faults)

To trigger the SRE Copilot into action, you can inject deliberate faults into the Demo Application via the Admin UI. This simulates realistic production incidents.

1. Open `http://localhost:8000/admin/faults` in your browser.
2. Select a fault type (e.g., Memory Leak, Database Latency, or 500 Error Spikes).
3. Wait for the Copilot's scheduled watcher to pick up the anomaly based on the alert thresholds. 
4. The Copilot will generate a Root Cause Analysis (RCA) and send an alert to your configured Discord Webhook.
